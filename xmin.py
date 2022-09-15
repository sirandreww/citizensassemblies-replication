# coding: utf-8
# This file is taken from the Sortition Foundation's stratification-app, specifically from
# https://github.com/pgoelz/stratification-app/blob/e6462ca084e/stratification.py .
# The file has been adapted by removing methods and dependencies that are not needed to run the experiments.
# Original file written by Brett Hennig bsh [AT] sortitionfoundation.org and Paul Gölz pgoelz (AT) cs.cmu.edu

"""
***********************************************************************************************************************
    imports
***********************************************************************************************************************
"""

import typing
from typing import Dict, List, Tuple, FrozenSet, Iterable, Optional, Set

import gurobipy as grb
import mip
import numpy as np
from legacy import SelectionError
from leximin import InfeasibleQuotasError
from leximin import find_distribution_leximin

"""
***********************************************************************************************************************
    globals
***********************************************************************************************************************
"""

# 0 means no debug message, higher number (could) mean more messages
debug = 0
# numerical deviation accepted as equality when dealing with solvers
EPS = 0.0005


"""
***********************************************************************************************************************
    helper functions 
***********************************************************************************************************************
"""


def _ilp_results_to_committee(variables: Dict[str, mip.entities.Var]) -> FrozenSet[str]:
    try:
        res = frozenset(id for id in variables if variables[id].x > 0.5)
    except Exception as e:  # unfortunately, MIP sometimes throws generic Exceptions rather than a subclass.
        raise ValueError(f"It seems like some variables does not have a value. Original exception: {e}.")

    return res


def _same_address(columns_data1: Dict[str, str], columns_data2: Dict[str, str], check_same_address_columns: List[str]) \
                 -> bool:
    return all(columns_data1[column] == columns_data2[column] for column in check_same_address_columns)


def _print(message: str) -> str:
    print(message)
    return message


def _compute_households(people: Dict[str, Dict[str, str]], columns_data: Dict[str, Dict[str, str]],
                        check_same_address_columns: List[str]) -> Dict[str, int]:
    ids = list(people.keys())
    households = {id: None for id in people}  # for each agent, the id of the earliest person with same address

    counter = 0
    for i, id1 in enumerate(ids):
        if households[id1] is not None:
            continue
        households[id1] = counter
        for id2 in ids[i + 1:]:
            if households[id2] is None and _same_address(columns_data[id1], columns_data[id2],
                                                         check_same_address_columns):
                households[id2] = counter
        counter += 1

    if counter == 1:
        print("Warning: All pool members live in the same household. Probably, the configuration is wrong?")

    return households


def _relax_infeasible_quotas(categories: Dict[str, Dict[str, Dict[str, int]]], people: Dict[str, Dict[str, str]],
                             number_people_wanted: int, check_same_address: bool,
                             households: Optional[Dict[str, int]] = None,
                             ensure_inclusion: typing.Collection[Iterable[str]] = ((),)) \
                             -> Tuple[Dict[Tuple[str, str], Tuple[int, int]], List[str]]:
    """Assuming that the quotas are not satisfiable, suggest a minimal relaxation that would be.

    Args:
        categories: quotas in the format described in `find_random_sample`
        people: pool members in the format described in `find_random_sample`
        number_people_wanted: desired size of the panel
        check_same_address: whether members from the same household cannot simultaneously appear
        households: if `check_same_address` is given, a dictionary mapping pool member ids to integers representing
            households. if two agents have the same value in the dictionary, they are considered to live together.
        ensure_inclusion: allows to specify that some panels should contain specific sets of agents. for example,
            passing `(("a",), ("b", "c"))` means that the quotas should be relaxed such that some valid panel contains
            agent "a" and some valid panel contains both agents "b" and "c". the default of `((),)` just requires
            a panel to exist, without further restrictions.
    """
    model = mip.Model(sense=mip.MINIMIZE)
    model.verbose = debug

    assert len(ensure_inclusion) > 0  # otherwise, the existence of a panel is not required

    # for every feature, a variable for how much the upper and lower quotas are relaxed
    feature_values = [(feature, value) for feature in categories for value in categories[feature]]
    min_vars = {fv: model.add_var(var_type=mip.INTEGER, lb=0.) for fv in feature_values}
    max_vars = {fv: model.add_var(var_type=mip.INTEGER, lb=0.) for fv in feature_values}

    # we might not be able to select multiple persons from the same household
    if check_same_address:
        assert households is not None

        people_by_household = {}
        for id, household in households.items():
            if household not in people_by_household:
                people_by_household[household] = []
            people_by_household[household].append(id)

    for inclusion_set in ensure_inclusion:
        # for every person, we have a binary variable indicating whether they are in the committee
        agent_vars = {id: model.add_var(var_type=mip.BINARY) for id in people}
        for agent in inclusion_set:
            model.add_constr(agent_vars[agent] == 1)

        # we have to select exactly `number_people_wanted` many persons
        model.add_constr(mip.xsum(agent_vars.values()) == number_people_wanted)

        # we have to respect the relaxed quotas quotas
        for feature, value in feature_values:
            number_feature_value_agents = mip.xsum(agent_vars[id] for id, person in people.items()
                                                   if person[feature] == value)
            model.add_constr(
                number_feature_value_agents >= categories[feature][value]["min"] - min_vars[(feature, value)])
            model.add_constr(
                number_feature_value_agents <= categories[feature][value]["max"] + max_vars[(feature, value)])

            if check_same_address:
                for household, members in people_by_household.items():
                    if len(members) >= 2:
                        model.add_constr(mip.xsum(agent_vars[id] for id in members) <= 1)

    def reduction_weight(feature, value):
        """Make the algorithm more recluctant to reduce lower quotas that are already low. If the lower quotas was 1,
        reducing it one more (to 0) is 3 times more salient than increasing a quota by 1. This bonus tampers off
        quickly, reducing from 10 is only 1.2 times as salient as an increase."""
        old_quota = categories[feature][value]["min"]
        if old_quota == 0:
            return 0  # cannot be relaxed anyway
        else:
            return 1 + 2/old_quota

    # we want to minimize the amount by which we have to relax quotas
    model.objective = mip.xsum([reduction_weight(*fv) * min_vars[fv] for fv in feature_values] + [max_vars[fv] for fv in feature_values])

    # Optimize once without any constraints to check if no feasible committees exist at all.
    status = model.optimize()
    if status != mip.OptimizationStatus.OPTIMAL:
        raise SelectionError(f"No feasible committees found, solver returns code {status} (see "
                             f"https://docs.python-mip.com/en/latest/classes.html#optimizationstatus). Either the pool "
                             f"is very bad or something is wrong with the solver.")

    output_lines = []
    new_quotas = {}
    for fv in feature_values:
        feature, value = fv
        lower = categories[feature][value]["min"] - round(min_vars[fv].x)
        assert lower <= categories[feature][value]["min"]
        if lower < categories[feature][value]["min"]:
            output_lines.append(f"Recommend lowering lower quota of {feature}:{value} to {lower}.")
        upper = categories[feature][value]["max"] + round(max_vars[fv].x)
        assert upper >= categories[feature][value]["max"]
        if upper > categories[feature][value]["max"]:
            assert lower == categories[feature][value]["min"]
            output_lines.append(f"Recommend raising upper quota of {feature}:{value} to {upper}.")
        new_quotas[fv] = (lower, upper)

    return new_quotas, output_lines


def _setup_committee_generation(categories: Dict[str, Dict[str, Dict[str, int]]], people: Dict[str, Dict[str, str]],
                                number_people_wanted: int, check_same_address: bool,
                                households: Optional[Dict[str, int]]) \
                               -> Tuple[mip.model.Model, Dict[str, mip.entities.Var]]:
    model = mip.Model(sense=mip.MAXIMIZE)
    model.verbose = debug

    # for every person, we have a binary variable indicating whether they are in the committee
    agent_vars = {id: model.add_var(var_type=mip.BINARY) for id in people}

    # we have to select exactly `number_people_wanted` many persons
    model.add_constr(mip.xsum(agent_vars.values()) == number_people_wanted)

    # we have to respect quotas
    for feature in categories:
        for value in categories[feature]:
            number_feature_value_agents = mip.xsum(agent_vars[id] for id, person in people.items()
                                                   if person[feature] == value)
            model.add_constr(number_feature_value_agents >= categories[feature][value]["min"])
            model.add_constr(number_feature_value_agents <= categories[feature][value]["max"])

    # we might not be able to select multiple persons from the same household
    if check_same_address:
        people_by_household = {}
        for id, household in households.items():
            if household not in people_by_household:
                people_by_household[household] = []
            people_by_household[household].append(id)

        for household, members in people_by_household.items():
            if len(members) >= 2:
                model.add_constr(mip.xsum(agent_vars[id] for id in members) <= 1)

    # Optimize once without any constraints to check if no feasible committees exist at all.
    status = model.optimize()
    if status == mip.OptimizationStatus.INFEASIBLE:
        new_quotas, output_lines = _relax_infeasible_quotas(categories, people, number_people_wanted,
                                                            check_same_address, households)
        raise InfeasibleQuotasError(new_quotas, output_lines)
    elif status != mip.OptimizationStatus.OPTIMAL:
        raise SelectionError(f"No feasible committees found, solver returns code {status} (see "
                             "https://docs.python-mip.com/en/latest/classes.html#optimizationstatus).")

    return model, agent_vars


def _generate_initial_committees(new_committee_model: mip.model.Model, agent_vars: Dict[str, mip.entities.Var],
                                 multiplicative_weights_rounds: int) \
                                -> Tuple[Set[FrozenSet[str]], FrozenSet[str], List[str]]:
    """To speed up the main iteration of the maximin and Nash algorithms, start from a diverse set of feasible
    committees. In particular, each agent that can be included in any committee will be included in at least one of
    these committees.
    """
    new_output_lines = []
    committees: Set[FrozenSet[str]] = set()  # Committees discovered so far
    covered_agents: Set[str] = set()  # All agents included in some committee

    # We begin using a multiplicative-weight stage. Each agent has a weight starting at 1.
    weights = {id: 1 for id in agent_vars}
    for i in range(multiplicative_weights_rounds):
        # In each round, we find a
        # feasible committee such that the sum of weights of its members is maximal.
        new_committee_model.objective = mip.xsum(weights[id] * agent_vars[id] for id in agent_vars)
        new_committee_model.optimize()
        new_set = _ilp_results_to_committee(agent_vars)

        # We then decrease the weight of each agent in the new committee by a constant factor. As a result, future
        # rounds will strongly prioritize including agents that appear in few committees.
        for id in new_set:
            weights[id] *= 0.8
        # We rescale the weights, which does not change the conceptual algorithm but prevents floating point problems.
        coefficient_sum = sum(weights.values())
        for id in agent_vars:
            weights[id] *= len(agent_vars) / coefficient_sum

        if new_set not in committees:
            # We found a new committee, and repeat.
            committees.add(new_set)
            for id in new_set:
                covered_agents.add(id)
        else:
            # If our committee is already known, make all weights a bit more equal again to mix things up a little.
            for id in agent_vars:
                weights[id] = 0.9 * weights[id] + 0.1

        print(f"Multiplicative weights phase, round {i+1}/{multiplicative_weights_rounds}. Discovered {len(committees)}"
              " committees so far.")

    # If there are any agents that have not been included so far, try to find a committee including this specific agent.
    for id in agent_vars:
        if id not in covered_agents:
            new_committee_model.objective = agent_vars[id]  # only care about agent `id` being included.
            new_committee_model.optimize()
            new_set: FrozenSet[str] = _ilp_results_to_committee(agent_vars)
            if id in new_set:
                committees.add(new_set)
                for id2 in new_set:
                    covered_agents.add(id2)
            else:
                new_output_lines.append(_print(f"Agent {id} not contained in any feasible committee."))

    # We assume in this stage that the quotas are feasible.
    assert len(committees) >= 1

    if len(covered_agents) == len(agent_vars):
        new_output_lines.append(_print("All agents are contained in some feasible committee."))

    return committees, frozenset(covered_agents), new_output_lines


def _dual_leximin_stage(people: Dict[str, Dict[str, str]], committees: Set[FrozenSet[str]],
                        fixed_probabilities: Dict[str, float]):
    """This implements the dual LP described in `find_distribution_leximin`, but where P only ranges over the panels
    in `committees` rather than over all feasible panels:
    minimize ŷ - Σ_{i in fixed_probabilities} fixed_probabilities[i] * yᵢ
    s.t.     Σ_{i ∈ P} yᵢ ≤ ŷ                              ∀ P
             Σ_{i not in fixed_probabilities} yᵢ = 1
             ŷ, yᵢ ≥ 0                                     ∀ i

    Returns a Tuple[grb.Model, Dict[str, grb.Var], grb.Var]   (not in type signature to prevent global gurobi import.)
    """
    assert len(committees) != 0

    model = grb.Model()
    agent_vars = {person: model.addVar(vtype=grb.GRB.CONTINUOUS, lb=0.) for person in people}  # yᵢ
    cap_var = model.addVar(vtype=grb.GRB.CONTINUOUS, lb=0.)  # ŷ
    model.addConstr(grb.quicksum(agent_vars[person] for person in people if person not in fixed_probabilities) == 1)
    for committee in committees:
        model.addConstr(grb.quicksum(agent_vars[person] for person in committee) <= cap_var)
    model.setObjective(cap_var - grb.quicksum(
                                    fixed_probabilities[person] * agent_vars[person] for person in fixed_probabilities),
                       grb.GRB.MINIMIZE)

    # Change Gurobi configuration to encourage strictly complementary (“inner”) solutions. These solutions will
    # typically allow to fix more probabilities per outer loop of the leximin algorithm.
    model.setParam("Method", 2)  # optimize via barrier only
    model.setParam("Crossover", 0)  # deactivate cross-over

    return model, agent_vars, cap_var


def _expand_distribution_leximin(
        categories: Dict[str, Dict[str, Dict[str, int]]],
        people: Dict[str, Dict[str, str]],
        columns_data: Dict[str, Dict[str, str]],
        number_people_wanted: int,
        check_same_address: bool,
        check_same_address_columns: List[str],
        initial_committees: Set[FrozenSet[str]],
        initial_covered_agents: FrozenSet[int]
) \
                             -> Tuple[List[FrozenSet[str]], List[float], List[str]]:
    """Find a distribution over feasible committees that maximizes the minimum probability of an agent being selected
    (just like maximin), but breaks ties to maximize the second-lowest probability, breaks further ties to maximize the
    third-lowest probability and so forth.

    Arguments follow the pattern of `find_random_sample`.

    Returns:
        (committees, probabilities, output_lines)
        `committees` is a list of feasible committees, where each committee is represented by a frozen set of included
            agent ids.
        `probabilities` is a list of probabilities of equal length, describing the probability with which each committee
            should be selected.
        `output_lines` is a list of debug strings.
    """
    output_lines = ["Using leximin algorithm."]
    grb.setParam("OutputFlag", 0)

    if check_same_address:
        households = _compute_households(people, columns_data, check_same_address_columns)
    else:
        households = None

    # Set up an ILP `new_committee_model` that can be used for discovering new feasible committees maximizing some
    # sum of weights over the agents.
    new_committee_model, agent_vars = _setup_committee_generation(categories, people, number_people_wanted,
                                                                  check_same_address, households)

    # Start by finding some initial committees, guaranteed to cover every agent that can be covered by some committee
    committees: Set[FrozenSet[str]]  # set of feasible committees, add more over time
    covered_agents: FrozenSet[str]  # all agent ids for agents that can actually be included
    committees, covered_agents = (initial_committees, initial_covered_agents)

    # Over the course of the algorithm, the selection probabilities of more and more agents get fixed to a certain value
    fixed_probabilities: Dict[str, float] = {}

    reduction_counter = 0

    # The outer loop maximizes the minimum of all unfixed probabilities while satisfying the fixed probabilities.
    # In each iteration, at least one more probability is fixed, but often more than one.
    while len(fixed_probabilities) < len(people):
        print(f"Fixed {len(fixed_probabilities)}/{len(people)} probabilities.")

        dual_model, dual_agent_vars, dual_cap_var = _dual_leximin_stage(people, committees, fixed_probabilities)
        # In the inner loop, there is a column generation for maximizing the minimum of all unfixed probabilities
        while True:
            """The primal LP being solved by column generation, with a variable x_P for each feasible panel P:
            
            maximize z
            s.t.     Σ_{P : i ∈ P} x_P ≥ z                         ∀ i not in fixed_probabilities
                     Σ_{P : i ∈ P} x_P ≥ fixed_probabilities[i]    ∀ i in fixed_probabilities
                     Σ_P x_P ≤ 1                                   (This should be thought of as equality, and wlog.
                                                                   optimal solutions have equality, but simplifies dual)
                     x_P ≥ 0                                       ∀ P
                     
            We instead solve its dual linear program:
            minimize ŷ - Σ_{i in fixed_probabilities} fixed_probabilities[i] * yᵢ
            s.t.     Σ_{i ∈ P} yᵢ ≤ ŷ                              ∀ P
                     Σ_{i not in fixed_probabilities} yᵢ = 1
                     ŷ, yᵢ ≥ 0                                     ∀ i
            """
            dual_model.optimize()
            if dual_model.status != grb.GRB.OPTIMAL:
                # In theory, the LP is feasible in the first iterations, and we only add constraints (by fixing
                # probabilities) that preserve feasibility. Due to floating-point issues, however, it may happen that
                # Gurobi still cannot satisfy all the fixed probabilities in the primal (meaning that the dual will be
                # unbounded). In this case, we slightly relax the LP by slightly reducing all fixed probabilities.
                for agent in fixed_probabilities:
                    # Relax all fixed probabilities by a small constant
                    fixed_probabilities[agent] = max(0., fixed_probabilities[agent] - 0.0001)
                    dual_model, dual_agent_vars, dual_cap_var = _dual_leximin_stage(people, committees,
                                                                                    fixed_probabilities)
                print(dual_model.status, f"REDUCE PROBS for {reduction_counter}th time.")
                reduction_counter += 1
                continue

            # Find the panel P for which Σ_{i ∈ P} yᵢ is largest, i.e., for which Σ_{i ∈ P} yᵢ ≤ ŷ is tightest
            agent_weights = {person: agent_var.x for person, agent_var in dual_agent_vars.items()}
            new_committee_model.objective = mip.xsum(agent_weights[person] * agent_vars[person] for person in people)
            new_committee_model.optimize()
            new_set = _ilp_results_to_committee(agent_vars)  # panel P
            value = new_committee_model.objective_value  # Σ_{i ∈ P} yᵢ

            upper = dual_cap_var.x  # ŷ
            dual_obj = dual_model.objVal  # ŷ - Σ_{i in fixed_probabilities} fixed_probabilities[i] * yᵢ

            output_lines.append(_print(f"Maximin is at most {dual_obj - upper + value:.2%}, can do {dual_obj:.2%} with "
                                       f"{len(committees)} committees. Gap {value - upper:.2%}."))
            if value <= upper + EPS:
                # Within numeric tolerance, the panels in `committees` are enough to constrain the dual, i.e., they are
                # enough to support an optimal primal solution.
                for person, agent_weight in agent_weights.items():
                    if agent_weight > EPS and person not in fixed_probabilities:
                        # `agent_weight` is the dual variable yᵢ of the constraint "Σ_{P : i ∈ P} x_P ≥ z" for
                        # i = `person` in the primal LP. If yᵢ is positive, this means that the constraint must be
                        # binding in all optimal solutions [1], and we can fix `person`'s probability to the
                        # optimal value of the primal/dual LP.
                        # [1] Theorem 3.3 in: Renato Pelessoni. Some remarks on the use of the strict complementarity in
                        # checking coherence and extending coherent probabilities. 1998.
                        fixed_probabilities[person] = max(0, dual_obj)
                break
            else:
                # Given that Σ_{i ∈ P} yᵢ > ŷ, the current solution to `dual_model` is not yet a solution to the dual.
                # Thus, add the constraint for panel P and recurse.
                assert new_set not in committees
                committees.add(new_set)
                dual_model.addConstr(grb.quicksum(dual_agent_vars[id] for id in new_set) <= dual_cap_var)

    # The previous algorithm computed the leximin selection probabilities of each agent and a set of panels such that
    # the selection probabilities can be obtained by randomizing over these panels. Here, such a randomization is found.
    primal = grb.Model()
    # Variables for the output probabilities of the different panels
    committee_vars = [primal.addVar(vtype=grb.GRB.CONTINUOUS, lb=0.) for _ in committees]
    # To avoid numerical problems, we formally minimize the largest downward deviation from the fixed probabilities.
    eps = primal.addVar(vtype=grb.GRB.CONTINUOUS, lb=0.)
    primal.addConstr(grb.quicksum(committee_vars) == 1)  # Probabilities add up to 1
    for person, prob in fixed_probabilities.items():
        person_probability = grb.quicksum(comm_var for committee, comm_var in zip(committees, committee_vars)
                                          if person in committee)
        primal.addConstr(person_probability >= prob - eps)
    primal.setObjective(eps + grb.quicksum([p ** 2 for p in committee_vars]), grb.GRB.MINIMIZE)
    primal.optimize()

    # Bound variables between 0 and 1 and renormalize, because np.random.choice is sensitive to small deviations here
    probabilities = np.array([comm_var.x for comm_var in committee_vars]).clip(0, 1)
    probabilities = list(probabilities / sum(probabilities))

    return list(committees), probabilities, output_lines


def _get_panel_not_in_portfolio_if_possible(categories, agents, k, portfolio):
    from analysis import legacy_find
    for _ in range(len(agents) * 3):
        panel = legacy_find(categories, agents, k)
        panel = frozenset(panel)
        if panel not in portfolio:
            return panel
        else:
            pass
    # not found
    return None


"""
***********************************************************************************************************************
    Function to get panel distribution
***********************************************************************************************************************
"""


def find_distribution_xmin(categories: Dict[str, Dict[str, Dict[str, int]]], people: Dict[str, Dict[str, str]],
                              columns_data: Dict[str, Dict[str, str]], number_people_wanted: int,
                              check_same_address: bool, check_same_address_columns: List[str]) \
                             -> Tuple[List[FrozenSet[str]], List[float], List[str]]:
    """
    Find a distribution over feasible committees that maximizes the minimum probability of an agent being selected
        (just like maximin), but breaks ties to maximize the second-lowest probability, breaks further ties to maximize the
        third-lowest probability and so forth.

        Arguments follow the pattern of `find_random_sample`.

        Returns:
            (committees, probabilities, output_lines)
            `committees` is a list of feasible committees, where each committee is represented by a frozen set of included
                agent ids.
            `probabilities` is a list of probabilities of equal length, describing the probability with which each committee
                should be selected.
            `output_lines` is a list of debug strings.
    """
    agents = people
    k = number_people_wanted

    initial_portfolio, probabilities, output_lines = find_distribution_leximin(
        categories, people, columns_data, number_people_wanted, check_same_address, check_same_address_columns
    )

    # try and expand the portfolio
    number_of_iterations = len(agents) * 5
    for i in range(number_of_iterations):
        print("Xmin iteration number", i, "out of", number_of_iterations)
        new_panel = _get_panel_not_in_portfolio_if_possible(
            categories=categories, agents=agents, k=k, portfolio=initial_portfolio
        )

        if new_panel is None:
            # stop loop
            break
        else:
            initial_portfolio += [new_panel]

        initial_committees = set(initial_portfolio)
        initial_covered_agents: Set[int] = set()

        # update initial_covered_agents
        assert isinstance(initial_committees, set)
        for com in initial_committees:
            if len(initial_covered_agents) == len(people):
                break
            assert isinstance(com, frozenset)
            for elem in com:
                assert isinstance(elem, int)
                if elem not in initial_covered_agents:
                    initial_covered_agents.add(elem)

        initial_portfolio, probabilities, output_lines = _expand_distribution_leximin(
            categories, people, columns_data, number_people_wanted, check_same_address, check_same_address_columns,
            initial_committees,
            frozenset(initial_covered_agents)
        )

    return initial_portfolio, probabilities, output_lines

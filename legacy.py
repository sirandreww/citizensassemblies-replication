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

import random
from typing import Dict, List, Tuple


"""
***********************************************************************************************************************
    globals
***********************************************************************************************************************
"""

# 0 means no debug message, higher number (could) mean more messages
debug = 0

"""
***********************************************************************************************************************
    class that houses Error of no panel found
***********************************************************************************************************************
"""


# class for throwing error/fail exceptions
class SelectionError(Exception):
    def __init__(self, message):
        self.msg = message


"""
***********************************************************************************************************************
    helper functions 
***********************************************************************************************************************
"""


# when a category is full we want to delete everyone in it
def delete_all_in_cat(categories, people, cat, cat_value):
    people_to_delete = []
    for pkey, person in people.items():
        if person[cat] == cat_value:
            people_to_delete.append(pkey)
            for pcat, pval in person.items():
                cat_item = categories[pcat][pval]
                cat_item["remaining"] -= 1
                if cat_item["remaining"] == 0 and cat_item["selected"] < cat_item["min"]:
                    raise SelectionError(
                        "FAIL in delete_all_in_cat: no one/not enough left in " + pval
                    )
    for p in people_to_delete:
        del people[p]
    # return the number of people deleted and the number of people left
    return len(people_to_delete), len(people)


# selected = True means we are deleting because they have been chosen,
# otherwise they are being deleted because they live at same address as someone selected
def really_delete_person(categories, people, pkey, selected):
    for pcat, pval in people[pkey].items():
        cat_item = categories[pcat][pval]
        if selected:
            cat_item["selected"] += 1
        cat_item["remaining"] -= 1
        if cat_item["remaining"] == 0 and cat_item["selected"] < cat_item["min"]:
            raise SelectionError("FAIL in delete_person: no one left in " + pval)
    del people[pkey]


def get_people_at_same_address(people, pkey, columns_data, check_same_address_columns):
    # primary_address1 = columns_data[pkey]["primary_address1"]
    # primary_zip = columns_data[pkey]["primary_zip"]
    primary_address1 = columns_data[pkey][check_same_address_columns[0]]
    primary_zip = columns_data[pkey][check_same_address_columns[1]]
    # there may be multiple people to delete, and deleting them as we go gives an error
    people_to_delete = []
    output_lines = []
    for compare_key in people.keys():
        if (
            # primary_address1 == columns_data[compare_key]["primary_address1"]
            # and primary_zip == columns_data[compare_key]["primary_zip"]
            primary_address1 == columns_data[compare_key][check_same_address_columns[0]]
            and primary_zip == columns_data[compare_key][check_same_address_columns[1]]
        ):
            # found same address
            output_lines += [
                "Found someone with the same address as a selected person,"
                " so deleting him/her. Address: {} , {}".format(primary_address1, primary_zip)
            ]
            people_to_delete.append(compare_key)
    return people_to_delete, output_lines


# lucky person has been selected - delete person from DB
def delete_person(categories, people, pkey, columns_data, check_same_address, check_same_address_columns):
    output_lines = []
    # recalculate all category values that this person was in
    person = people[pkey]
    really_delete_person(categories, people, pkey, True)
    # check if there are other people at the same address - if so, remove them!
    if check_same_address:
        people_to_delete, output_lines = get_people_at_same_address(people, pkey, columns_data, check_same_address_columns)
        # then delete this/these people at the same address
        for del_person_key in people_to_delete:
            really_delete_person(categories, people, del_person_key, False)
    # then check if any cats of selected person is (was) in are full
    for (pcat, pval) in person.items():
        cat_item = categories[pcat][pval]
        if cat_item["selected"] == cat_item["max"]:
            num_deleted, num_left = delete_all_in_cat(categories, people, pcat, pval)
            output_lines += [ "Category {} full - deleted {}, {} left.".format(pval, num_deleted, num_left) ]
    return output_lines


# returns dict of category key, category item name, random person number
def find_max_ratio_cat(categories):
    ratio = -100.0
    key_max = ""
    index_max_name = ""
    random_person_num = -1
    for cat_key, cats in categories.items():
        for cat, cat_item in cats.items():
            # if there are zero remaining, or if there are less than how many we need we're in trouble
            if cat_item["selected"] < cat_item["min"] and cat_item["remaining"] < (
                cat_item["min"] - cat_item["selected"]
            ):
                raise SelectionError(
                    "FAIL in find_max_ratio_cat: No people (or not enough) in category " + cat
                )
            # if there are none remaining, it must be because we have reached max and deleted them
            # or, if max = 0, then we don't want any of these (could happen when seeking replacements)
            if cat_item["remaining"] != 0 and cat_item["max"] != 0:
                item_ratio = (cat_item["min"] - cat_item["selected"]) / float(cat_item["remaining"])
                # print item['name'],': ', item['remaining'], 'ratio : ', item_ratio
                if item_ratio > 1:  # trouble!
                    raise SelectionError("FAIL in find_max_ratio_cat: a ratio > 1...")
                if item_ratio > ratio:
                    ratio = item_ratio
                    key_max = cat_key
                    index_max_name = cat
                    random_person_num = random.randint(1, cat_item["remaining"])
    if debug > 0:
        print("Max ratio: {} for {} {}".format(ratio, key_max, index_max_name))
        # could also append random_person_num
    return {
        "ratio_cat": key_max,
        "ratio_cat_val": index_max_name,
        "ratio_random": random_person_num,
    }


def check_min_cats(categories):
    output_msg = []
    got_min = True
    for cat_key, cats in categories.items():
        for cat, cat_item in cats.items():
            if cat_item["selected"] < cat_item["min"]:
                got_min = False
                output_msg = ["Failed to get minimum in category: {}".format(cat)]
    return got_min, output_msg


"""
***********************************************************************************************************************
    Function to get panel.
***********************************************************************************************************************
"""


def find_random_sample_legacy(categories: Dict[str, Dict[str, Dict[str, int]]], people: Dict[str, Dict[str, str]],
                              columns_data: Dict[str, Dict[str, str]], number_people_wanted: int,
                              check_same_address: bool, check_same_address_columns: List[str]) \
                             -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    output_lines = ["Using legacy algorithm."]
    people_selected = {}
    for count in range(number_people_wanted):
        ratio = find_max_ratio_cat(categories)
        # find randomly selected person with the category value
        for pkey, pvalue in people.items():
            if pvalue[ratio["ratio_cat"]] == ratio["ratio_cat_val"]:
                # found someone with this category value...
                ratio["ratio_random"] -= 1
                if ratio["ratio_random"] == 0:  # means they are the random one we want
                    if debug > 0:
                        print("Found random person in this cat... adding them")
                    people_selected.update({pkey: pvalue})
                    output_lines += delete_person(categories, people, pkey, columns_data, check_same_address,
                                                  check_same_address_columns)
                    break
        if count < (number_people_wanted - 1) and len(people) == 0:
            raise SelectionError("Fail! We've run out of people...")
    return people_selected, output_lines


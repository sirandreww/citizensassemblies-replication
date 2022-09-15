import csv
import itertools
def generate_categories(list_of_categories, list_of_list_of_features, list_of_list_of_quotas,file_name):
    with open(file_name, 'w',newline='') as f:
        writer = csv.writer(f)
        init_row = ["category","feature","min","max"]
        writer.writerow(init_row)
        for i in range(len(list_of_categories)):
            features = list_of_list_of_features[i]
            quotas = list_of_list_of_quotas[i]
            for j in range(len(features)):
                row = []
                row.append(list_of_categories[i])
                row.append(features[j])
                row.append(quotas[j][0])
                row.append(quotas[j][1])
                writer.writerow(row)

def generate_all_possible_join_features(features):
    my_list = []
    for l in itertools.product(*features):
        my_list.append(list(l))
    return my_list


def generate_respondents(list_of_features ,list_of_joint_features, list_of_num,file_name):
    with open(file_name, 'w',newline='') as f:
        writer = csv.writer(f)
        init_row = list_of_features
        writer.writerow(init_row)
        for i in range(len(list_of_joint_features)):
            for j in range(list_of_num[i]):
                to_add = list(list_of_joint_features[i])
                writer.writerow(to_add)


def main():
    categories = ["gender","politics","education"]
    features = [["female","non-binary","male"],["right","left","center"],["higher education","no higher education"]]
    quotas = [[[5,10],[2,4],[5,10]],[[2,3],[1,5],[2,3]],[[2,3],[5,10]]]
    my_feature_list = generate_all_possible_join_features(features)
    number_of_each = [1,10,6,4,8,3,9,1,10,4,10,11,12,3,5,2,5,3]
    generate_categories(categories,features,quotas,"categories.cvs")
    generate_respondents(categories,my_feature_list,number_of_each,"respondentes.cvs")

if __name__ == '__main__':
    main()


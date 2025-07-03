# check_switching_classes.py
import os
from sage.all import *

def seidel_matrix(T):
    A = T.adjacency_matrix()
    return A - A.transpose()

def mckay_matrix(S):
    S = seidel_matrix(S)
    n = S.nrows()
    D = zero_matrix(2 * n)
    for i in range(n):
        for j in range(n):
            if S[i, j] == 1:
                D[2*i, 2*j] = 1
                D[2*i+1, 2*j+1] = 1
            elif S[i, j] == -1:
                D[2*i, 2*j+1] = 1
                D[2*i+1, 2*j] = 1
    return D

def mckay_check(A, B):
    return DiGraph(A).is_isomorphic(DiGraph(B))

def deserialize_tournament(g6):
    return DiGraph(g6.strip())

def check_class_file(filepath):
    with open(filepath, 'r') as f:
        tourns = [deserialize_tournament(line) for line in f]

    if len(tourns) <= 1:
        return "trivial"

    for i in range(len(tourns)):
        for j in range(i + 1, len(tourns)):
            A = mckay_matrix(tourns[i])
            B = mckay_matrix(tourns[j])
            if not mckay_check(A, B):
                return f"not equivalent: T{i} vs T{j}"
    return "all equivalent"

def run_check(n, output_dir):
    class_dir = os.path.join(output_dir, f"n{n}")
    results_dir = os.path.join(class_dir, "results")
    os.makedirs(results_dir, exist_ok=True)

    for fname in sorted(os.listdir(class_dir)):
        if not fname.startswith("class_") or not fname.endswith(".txt"):
            continue

        result_file = os.path.join(results_dir, fname.replace(".txt", ".result"))
        if os.path.exists(result_file):
            continue  # Resume support

        status = check_class_file(os.path.join(class_dir, fname))
        with open(result_file, 'w') as rf:
            rf.write(status + "\n")

        print(f"{fname}: {status}")

if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "tournament_outputs_by_class"
    run_check(n, output_dir)

# generate_by_charpoly.py
import os, hashlib
from sage.all import *

def seidel_matrix(T):
    A = T.adjacency_matrix()
    return A - A.transpose()

def hash_charpoly(poly):
    return hashlib.md5(str(poly).encode()).hexdigest()[:8]

def serialize_tournament(T):
    return T.graph6_string()

def generate_and_split_by_charpoly(n, output_dir):
    class_dir = os.path.join(output_dir, f"n{n}")
    os.makedirs(class_dir, exist_ok=True)

    print(f"Generating tournaments for order n = {n}")
    count = 0
    for T in digraphs.tournaments_nauty(n):
        poly = seidel_matrix(T).charpoly()
        h = hash_charpoly(poly)
        filename = os.path.join(class_dir, f"class_{h}.txt")
        with open(filename, "a") as f:
            f.write(serialize_tournament(T) + "\n")
        count += 1
        if count % 100 == 0:
            print(f"{count} tournaments processed...")

    print(f"Finished generating tournaments for n = {n}")

if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 11
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "tournament_outputs_by_class"
    generate_and_split_by_charpoly(n, output_dir)

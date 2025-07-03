import math, random, os, re, hashlib
from collections import defaultdict
from sage.all import *

output_dir = "tournament_outputs_new"
os.makedirs(output_dir, exist_ok=True)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

def seidel_matrix(T):
    A = T.adjacency_matrix()
    return A - A.transpose()

def switching_operator(G, U):
    V = set(G.vertices())
    Uc = V.difference(U)
    H = G.copy()
    for u in U:
        for v in Uc:
            if H.has_edge(u, v):
                H.delete_edge(u, v)
                H.add_edge(v, u)
            elif H.has_edge(v, u):
                H.delete_edge(v, u)
                H.add_edge(u, v)
    return H

def mckay_matrix(S):
    S = seidel_matrix(S)
    nrows, ncols = S.nrows(), S.ncols()
    D_S = zero_matrix(2 * nrows, 2 * ncols)
    for i in range(nrows):
        for j in range(ncols):
            if S[i, j] == 1:
                D_S[2 * i, 2 * j] = 1
                D_S[2 * i + 1, 2 * j + 1] = 1
            elif S[i, j] == -1:
                D_S[2 * i, 2 * j + 1] = 1
                D_S[2 * i + 1, 2 * j] = 1
    return D_S

def mckay_check(A, B):
    return DiGraph(A).is_isomorphic(DiGraph(B))

def poly_hash(poly):
    return hashlib.md5(str(poly).encode()).hexdigest()[:8]

class Tee:
    def __init__(self, base_filename):
        self.base_filename = base_filename
        self.files = [sys.__stdout__]
        self.current_file = None
        self.part = self._find_last_available_part()
        self._open_file_at_part()

    def _find_last_available_part(self):
        i = 1
        while True:
            fname = f"{self.base_filename}_part{i}.txt"
            if not os.path.exists(fname):
                return i
            if os.path.getsize(fname) < MAX_FILE_SIZE:
                return i
            i += 1

    def _open_file_at_part(self):
        file_path = f"{self.base_filename}_part{self.part}.txt"
        self.current_file = open(file_path, "a")
        self.files = [sys.__stdout__, self.current_file]
        print(f"\n--- Writing to {file_path} ---", file=sys.__stdout__)

    def write(self, obj):
        if os.path.getsize(self.current_file.name) > MAX_FILE_SIZE:
            self.current_file.close()
            self.part += 1
            self._open_file_at_part()
        for f in self.files:
            f.write(obj)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()

def get_last_completed_charpoly_index(n, output_dir):
    next_prefix = f"tournaments_n_{n+1}_part"
    if any(f.startswith(next_prefix) for f in os.listdir(output_dir)):
        return 'complete'

    prefix = f"tournaments_n_{n}_part"
    part_files = sorted(
        [f for f in os.listdir(output_dir) if f.startswith(prefix)],
        key=lambda fname: int(re.findall(r'part(\d+)', fname)[0])
    )

    last_index = 0
    for fname in reversed(part_files):
        with open(os.path.join(output_dir, fname), 'r') as f:
            for line in reversed(f.readlines()):
                if '### Charpoly Class' in line:
                    match = re.search(r'### Charpoly Class (\d+)', line)
                    if match:
                        return int(match.group(1))
    return 0

# --- Main loop ---
for n in range(11, 50 + 1):
    resume_index = get_last_completed_charpoly_index(n, output_dir)
    if resume_index == 'complete':
        print(f"Order {n} is already complete. Skipping.")
        continue

    base_filename = os.path.join(output_dir, f"tournaments_n_{n}")
    tee = Tee(base_filename)
    sys.stdout = tee

    print(f"\n================= Order n = {n} =================\n")
    tourns = list(digraphs.tournaments_nauty(n))
    poly_classes = defaultdict(list)

    for T in tourns:
        p = seidel_matrix(T).charpoly()
        poly_classes[p].append(T)

    total_classes = len(poly_classes)
    total_tourns = len(tourns)
    checked_classes = 0
    checked_tourns = 0

    for idx, (poly, class_tourns) in enumerate(poly_classes.items(), 1):
        if idx <= resume_index:
            checked_classes += 1
            checked_tourns += len(class_tourns)
            continue

        print(f"\n### Charpoly Class {idx} ###")
        print(f"Characteristic Polynomial: {poly}")
        print(f"Number of tournaments: {len(class_tourns)}")

        if len(class_tourns) == 1:
            print("Only one tournament - trivially switching equivalent.\n")
            checked_classes += 1
            checked_tourns += 1
            continue

        all_equiv = True
        for i in range(len(class_tourns)):
            for j in range(i + 1, len(class_tourns)):
                if not mckay_check(mckay_matrix(class_tourns[i]), mckay_matrix(class_tourns[j])):
                    print(f"Tournaments {i} and {j} are NOT switching equivalent (by McKay matrix isomorphism).")
                    print(f"Tournament {i}:\n{seidel_matrix(class_tourns[i])}")
                    print(f"Tournament {j}:\n{seidel_matrix(class_tourns[j])}")
                    all_equiv = False
                    break
            if not all_equiv:
                break

        if all_equiv:
            print("All tournaments in this class are mutually switching equivalent.\n")
        else:
            print("Not all tournaments in this class are switching equivalent.")
            print("Found a pair that is NOT switching equivalent. Skipping further checks in this class.\n")

        checked_classes += 1
        checked_tourns += len(class_tourns)

        # Print progress
        percent_classes = 100.0 * checked_classes / total_classes
        percent_tourns = 100.0 * checked_tourns / total_tourns
        print(f"Progress: Checked {checked_classes}/{total_classes} charpoly classes ({percent_classes:.2f}%)")
        print(f"         Checked {checked_tourns}/{total_tourns} tournaments ({percent_tourns:.2f}%)\n")

    sys.stdout = sys.__stdout__
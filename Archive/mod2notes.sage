for g in digraphs.tournaments_nauty(4):
    print(g.edges(sort=True, labels = False))
tournaments = digraphs.tournaments_nauty
[len(list(tournaments(x))) for x in range(1,8)]
[len(list(tournaments(x, strongly_connected = True))) for x in range(1,9)]
p.adjacency_matrix()
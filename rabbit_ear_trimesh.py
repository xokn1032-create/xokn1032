import trimesh
import numpy as np

vertices = np.array([
    [0, 100, 0],   
    [0, 80, 10],    
    [0, 50, 30],   
    [0, 20, 10],
    [-50, 50, 0],
    [50, 50, 0],
    [-40, 50, 10],
    [40, 50, 10],
    [-20, 0, 0],
    [20, 0, 0]
], dtype=float)

faces = np.array([
    [0, 1, 4],
    [0, 1, 5],
    [1, 4, 6],
    [1, 5, 7],
    [1, 6, 2],
    [1, 7, 2],
    [4, 6, 8],
    [5, 7, 9],
    [2, 6, 3],
    [2, 7, 3],
    [6, 3, 8],
    [7, 3, 9],
    [3, 8, 9]
])

rabbit_ear = trimesh.Trimesh(
    vertices=vertices,
    faces=faces
)

rabbit_ear.show()

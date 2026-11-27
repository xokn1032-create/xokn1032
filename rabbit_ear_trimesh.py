import trimesh
import numpy as np

vertices = np.array([
    [0, 100, 0],   
    [0, 80, 10],    
    [0, 50, 30],   
    [0, 20, 10],
    
], dtype=float)

faces = np.array([
    
])

rabbit_ear = trimesh.Trimesh(
    vertices=vertices,
    faces=faces
)

rabbit_ear.show()

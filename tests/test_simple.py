from cttilemap.classes import TileMap

with open("tests/0level.l", "rb") as f:
	print(TileMap.load(f))

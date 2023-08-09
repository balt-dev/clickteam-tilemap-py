from cttilemap import TileMap

with open("tests/382level.l", "rb") as f:
	t = TileMap.load(f)
with open("tests/382level-prime.l", "wb+") as f:
	t.dump(f)
with open("tests/382level-prime.l", "rb") as f:
	u = TileMap.load(f)

assert t == u
# cttilemap
### A pure-python library to parse [Clickteam TileMap](https://github.com/clickteam-plugin/TileMap/tree/master) files.
---
## How to use
```python
from cttilemap import TileMap, Tile

# Load from a file
with open("tilemap.l", "rb") as f:
    tmap = TileMap.load(f)

# Get a tile and its data
# Accessing the tile at x=3, y=5

layer = tmap.layers[0]
print(layer[3, 5])
for sublayer in layer.sublayers:
    print(f"- {sublayer[3, 5]}")

# Set a tile and its data
layer[3, 5] = Tile.by_id(0xBEEF)
layer.sublayers[0][3, 5] = b'\xFF'

tmap.layers[0] = layer

# Save to a file
with open("tilemap.l", "wb+") as f:
    tmap.dump(f)

```
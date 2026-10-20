"""Pantalla verde → PNG transparente. Verdor = G - max(R, B): el fondo es muy verde, el diseño
(morado, gris, blanco) no. Se suaviza la orilla y se quita el reflejo verde (despill)."""
import sys
import numpy as np
from PIL import Image
from scipy import ndimage

src, dst = sys.argv[1], sys.argv[2]
rgb = np.asarray(Image.open(src).convert("RGB")).astype(float)
r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
verdor = g - np.maximum(r, b)
# referencia: el verdor típico del fondo (esquinas)
ref = np.median(np.concatenate([verdor[:40, :40].ravel(), verdor[-40:, -40:].ravel(),
                                verdor[:40, -40:].ravel(), verdor[-40:, :40].ravel()]))
BAJO, ALTO = ref * 0.25, ref * 0.7          # bajo: opaco; alto: transparente
alfa = np.clip((ALTO - verdor) / (ALTO - BAJO), 0, 1)
# motas sueltas de fondo que quedaron opacas, y huecos dentro del diseño
opaco = alfa > 0.5
et, n = ndimage.label(opaco)
tam = ndimage.sum(opaco, et, range(1, n + 1))
alfa[np.isin(et, 1 + np.nonzero(tam < 40)[0])] = 0
# despill: lo verde que sobra en las orillas pasa a gris
g2 = np.minimum(g, (r + b) / 2 + 2)  # el diseño no tiene verde: nada pasa del promedio de R y B
out = np.dstack([r, g2, b, alfa * 255]).clip(0, 255).astype(np.uint8)
im = Image.fromarray(out, "RGBA")
im = im.crop(im.getbbox())
lado = max(im.size) + 16
cuadro = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
cuadro.paste(im, ((lado - im.width) // 2, (lado - im.height) // 2))
cuadro.save(dst)
print("verdor del fondo:", round(ref, 1), "| tamaño:", cuadro.size)

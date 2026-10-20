# Firma Electrónica

<img src="firmaelectronica/icono.png" alt="Ícono de Firma Electrónica" width="160" align="right">

Programa de escritorio para Linux para firmar documentos con **dos firmas a la vez**:

- **Firma dibujada:** la imagen de la firma de la persona (se dibuja con el mouse, touchpad o
  tableta, o se toma de una foto), con la leyenda «Firmado electrónicamente por … · fecha» debajo.
- **Firma electrónica:** un certificado propio por persona (RSA 3072 + SHA-256), protegido con su
  contraseña, que sella el PDF. Si alguien cambia el documento después de firmarlo, se nota al
  verificarlo.

> **Importante:** los certificados los genera el propio programa (autofirmados). Sirven para
> demostrar quién firmó y que el documento no se alteró, pero **no sustituyen** a una firma
> electrónica avanzada emitida por una autoridad (por ejemplo, la e.firma del SAT en México).

## Qué hace

- **Varias personas**, cada una con su nombre, cargo, dibujo de firma y **su propia contraseña**.
  Siempre se pide la contraseña para firmar y para cambiar el dibujo.
- **Muchos formatos:** PDF, Word (docx, doc, odt, rtf), Excel (xlsx, xls, ods), PowerPoint
  (pptx, ppt, odp), imágenes (jpg, png, webp, tif, bmp) y texto (txt). Todo se convierte a PDF
  y se firma; el resultado queda como `<nombre>-firmado.pdf` junto al original, que no se toca.
- **Vista de la hoja:** la firma aparece sobre la línea `_____` del documento (si la hay) y se
  puede **arrastrar**, **agrandar o achicar** (barra, esquina o rueda del mouse) y cambiar de
  página. Lo que se ve es exactamente lo que sale en el PDF.
- **Varias firmas en un mismo PDF:** después de firmar se puede pasar a «Que firme otra persona»;
  cada firma se agrega sin invalidar las anteriores.
- **Verificar:** muestra quién firmó, cuándo y si el PDF cambió después de cada firma.
- Clic derecho en Dolphin (KDE): «Firmar con Firma Electrónica» y «Verificar firma electrónica».

## Instalar

Requisitos: Linux con Python 3.13 y [uv](https://docs.astral.sh/uv/). Para firmar Word/Excel/
PowerPoint hace falta [OnlyOffice](https://www.onlyoffice.com/) (abierto al menos una vez) o
LibreOffice.

```sh
git clone https://github.com/Gatotote/FirmaElectronica.git
cd FirmaElectronica
./instalar.sh            # prepara el entorno y crea los accesos (menú, Escritorio, Dolphin)
./instalar.sh quitar     # quita los accesos
```

## Uso

1. **Firmas → ＋ Nueva firma:** nombre completo, cargo (opcional), contraseña y dibujo.
2. **Firmar documento → Abrir documento…**, elegir de quién es la firma, acomodarla y
   **Firmar documento** (pide la contraseña).
3. **Verificar → Elegir PDF firmado…**

Desde la terminal:

```sh
./ejecutar.sh [ventana [archivo ...]]
./ejecutar.sh verificar documento-firmado.pdf
./ejecutar.sh --version
```

## Dónde se guardan los datos

`~/.local/share/firma-electronica/firmas/<persona>/` (carpetas 700, archivos 600):
`certificado.p12` (llave privada, cifrada con la contraseña), `certificado.cer` (público),
`firma.png`, `perfil.json` y `anteriores/` (certificados renovados, para seguir verificando lo
ya firmado). Las firmas eliminadas pasan a `eliminadas/` por la misma razón.

**Las contraseñas no se pueden recuperar.** Si se olvida, se elimina esa firma y se crea otra;
lo firmado antes se sigue verificando.

## Hecho con

[PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0), [pyHanko](https://github.com/MatthiasValvekens/pyHanko) (MIT),
[PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0), [Pillow](https://python-pillow.org/) (MIT-CMU) y
[cryptography](https://cryptography.io/) (Apache-2.0 / BSD-3-Clause).

El código de este programa es MIT, pero **PyMuPDF es AGPL-3.0**: si redistribuyes el programa
empaquetado junto con sus dependencias, ese paquete debe cumplir la AGPL-3.0 (en la práctica:
compartir también el código fuente).

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y compartir este programa libremente, siempre que
se conserve la mención del autor.

Este programa fue realizado por **Genaro Velasco**.

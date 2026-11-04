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
- **Sello de tiempo:** al firmar se pide un sello de tiempo (RFC 3161) a DigiCert o Sectigo,
  gratis: la fecha la certifica un tercero, no el reloj de la PC. Solo viaja una huella (hash)
  de la firma, nunca el documento. Sin internet, el programa pregunta si firmar sin sello.
- **Revocar (robo o pérdida):** marca el certificado actual como revocado desde ese momento y
  crea uno nuevo. Lo firmado antes **con** sello de tiempo se sigue verificando bien; lo demás
  sale como «revocada».
- **Verificar:** muestra quién firmó, cuándo (y si la fecha viene del sello de tiempo), si el
  PDF cambió después de cada firma y si el certificado está revocado.
- **Para el papel: QR + folio.** Junto a cada firma se imprime un código QR y un folio
  (`FE-XXXX-XXXX`). La firma electrónica vive en el PDF; en papel solo queda la imagen, así que
  la hoja impresa **no se puede verificar por sí sola**. Con el QR o el folio se encuentra el
  original para compararlo.
- **Archivo:** cada documento firmado se guarda solo, con su folio. Se busca por folio, persona
  o documento; **«Revisar hoja impresa»** lee el QR de una foto o escaneo (imagen o PDF), abre el
  original y avisa si la huella del QR no coincide; **«Comparar con otro PDF»** dice si un PDF es
  exactamente el firmado, una versión anterior o un documento distinto. También detecta si la
  copia del archivo se modificó.
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
`certificado.llave` (llave privada) y `firma.png.cifrado` (dibujo), los dos **cifrados** con
AES-256-GCM y una llave sacada de la contraseña con **scrypt** (128 MB de memoria por intento,
así que probar contraseñas a fuerza bruta es muy lento); `certificado.cer` (público),
`perfil.json` y `anteriores/` (solo la parte pública de los certificados renovados, para seguir
verificando lo ya firmado). Las firmas eliminadas pasan a `eliminadas/` igual: solo con su parte
pública, sus llaves y su dibujo se destruyen. Los certificados revocados se anotan en
`revocados.json` y los documentos firmados en `archivo/<folio>/` (`firmado.pdf` + `datos.json`). Las contraseñas deben tener al menos 12 caracteres (puede ser una frase).

Las firmas de versiones anteriores (`certificado.p12` + `firma.png` sin cifrar) se pasan solas al
formato nuevo la primera vez que se escribe su contraseña.

**Las contraseñas no se pueden recuperar.** Si se olvida, se elimina esa firma y se crea otra;
lo firmado antes se sigue verificando.

## Hecho con

[PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0), [pyHanko](https://github.com/MatthiasValvekens/pyHanko) (MIT),
[PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL-3.0), [Pillow](https://python-pillow.org/) (MIT-CMU),
[cryptography](https://cryptography.io/) (Apache-2.0 / BSD-3-Clause), [segno](https://github.com/heuer/segno) (BSD-3-Clause)
y [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) (Apache-2.0).

El código de este programa es MIT, pero **PyMuPDF es AGPL-3.0**: si redistribuyes el programa
empaquetado junto con sus dependencias, ese paquete debe cumplir la AGPL-3.0 (en la práctica:
compartir también el código fuente).

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y compartir este programa libremente, siempre que
se conserve la mención del autor.

Este programa fue realizado por **Genaro Velasco**.

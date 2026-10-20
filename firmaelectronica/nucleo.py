"""Firma Electrónica: varias firmas (cada una con su dibujo, su certificado y su contraseña),
para firmar PDF, Word, Excel, OpenDocument, imágenes y texto (se convierten a PDF).

Uso:
    python -m firmaelectronica [ventana [documento ...]]   ventana (lo normal)
    python -m firmaelectronica ventana-verificar doc.pdf   ventana en «Verificar»
    python -m firmaelectronica verificar doc.pdf           revisar firmas desde la terminal
    python -m firmaelectronica --version

Cada firma digital usa un certificado propio (RSA 3072 + SHA-256): si alguien cambia el PDF
después, se ve al verificarlo. No tiene el peso legal de la e.firma del SAT.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

__version__ = "0.5.2"
CREDITO = "Este programa fue realizado por Genaro Velasco"
NOMBRE = "Firma Electrónica"

DATOS = Path.home() / ".local/share/firma-electronica"
FIRMAS = DATOS / "firmas"
FUENTE = next((f for f in ("/usr/share/fonts/noto/NotoSans-Regular.ttf",
                           "/usr/share/fonts/TTF/DejaVuSans.ttf",
                           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if Path(f).exists()), None)

# Formatos que se pueden firmar (todo se convierte a PDF antes de firmar).
EXT_PDF = {".pdf"}
EXT_OFFICE = {".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp"}
EXT_IMAGEN = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
EXT_TEXTO = {".txt"}
EXTENSIONES = EXT_PDF | EXT_OFFICE | EXT_IMAGEN | EXT_TEXTO

X2T = Path("/opt/onlyoffice/desktopeditors/converter/x2t")
X2T_FUENTES = Path.home() / ".local/share/onlyoffice/desktopeditors/data/fonts/AllFonts.js"

logging.getLogger("pyhanko").setLevel(logging.CRITICAL)  # el detalle técnico no le sirve al usuario


def _privado(ruta: Path, carpeta: bool = False) -> None:
    ruta.chmod(0o700 if carpeta else 0o600)


def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "firma"


# =====================================================================
#  Firmas (perfiles): cada una con nombre, cargo, dibujo y certificado
# =====================================================================

@dataclass
class Perfil:
    carpeta: Path

    @property
    def p12(self) -> Path: return self.carpeta / "certificado.p12"
    @property
    def cer(self) -> Path: return self.carpeta / "certificado.cer"
    @property
    def dibujo(self) -> Path: return self.carpeta / "firma.png"
    @property
    def anteriores(self) -> Path: return self.carpeta / "anteriores"

    def _datos(self) -> dict:
        try:
            return json.loads((self.carpeta / "perfil.json").read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    @property
    def nombre(self) -> str: return self._datos().get("nombre") or self.carpeta.name
    @property
    def cargo(self) -> str: return self._datos().get("cargo", "")

    def guardar_datos(self, nombre: str, cargo: str) -> None:
        archivo = self.carpeta / "perfil.json"
        archivo.write_text(json.dumps({"nombre": nombre, "cargo": cargo}, ensure_ascii=False, indent=2), "utf-8")
        _privado(archivo)

    def abrir(self, clave: str):
        """El firmante (llave privada) si la contraseña es la correcta; si no, None."""
        from pyhanko.sign import signers
        try:
            return signers.SimpleSigner.load_pkcs12(str(self.p12), passphrase=clave.encode())
        except Exception:
            return None

    def info(self) -> dict | None:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        if not self.cer.exists():
            return None
        c = x509.load_pem_x509_certificate(self.cer.read_bytes())
        return {"desde": c.not_valid_before_utc.astimezone(), "hasta": c.not_valid_after_utc.astimezone(),
                "huella": c.fingerprint(hashes.SHA256()).hex(":").upper()}

    def certificados(self) -> list[Path]:
        return [c for c in [self.cer, *sorted(self.anteriores.glob("*/certificado.cer"))] if c.exists()]

    def renovar(self, clave_actual: str, clave_nueva: str) -> str:
        if self.abrir(clave_actual) is None:
            raise ValueError("La contraseña actual no es correcta.")
        viejo = self.anteriores / dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        viejo.mkdir(parents=True)
        shutil.move(self.p12, viejo / self.p12.name)
        shutil.move(self.cer, viejo / self.cer.name)
        return _generar_certificado(self, clave_nueva)

    def eliminar(self) -> None:
        """La firma se va a papelera/ (no se borra): sus PDF ya firmados se siguen verificando."""
        destino = DATOS / "eliminadas" / f"{self.carpeta.name}-{dt.datetime.now():%Y%m%d-%H%M%S}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(self.carpeta, destino)


def listar_perfiles() -> list[Perfil]:
    _migrar_version_vieja()
    if not FIRMAS.is_dir():
        return []
    return sorted((Perfil(c) for c in FIRMAS.iterdir() if (c / "certificado.p12").exists()),
                  key=lambda p: p.nombre.lower())


def crear_perfil(nombre: str, cargo: str, clave: str) -> Perfil:
    nombre = " ".join(nombre.split())
    if not nombre:
        raise ValueError("Falta el nombre completo.")
    if len(clave) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    base = _slug(nombre)
    carpeta, n = FIRMAS / base, 2
    while carpeta.exists():
        carpeta, n = FIRMAS / f"{base}-{n}", n + 1
    carpeta.mkdir(parents=True)
    _privado(DATOS, carpeta=True)
    _privado(carpeta, carpeta=True)
    perfil = Perfil(carpeta)
    perfil.guardar_datos(nombre, cargo.strip())
    try:
        _generar_certificado(perfil, clave)
    except Exception:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    return perfil


def _generar_certificado(perfil: Perfil, clave: str) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if len(clave) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    llave = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    atributos = [x509.NameAttribute(NameOID.COMMON_NAME, perfil.nombre)]
    if perfil.cargo:
        atributos.append(x509.NameAttribute(NameOID.TITLE, perfil.cargo))
    nombre = x509.Name(atributos)
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(minutes=5))
        .not_valid_after(ahora + dt.timedelta(days=365 * 10))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False, crl_sign=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
        .sign(llave, hashes.SHA256())
    )
    perfil.p12.write_bytes(pkcs12.serialize_key_and_certificates(
        perfil.nombre.encode(), llave, cert, None, serialization.BestAvailableEncryption(clave.encode())))
    _privado(perfil.p12)
    perfil.cer.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert.fingerprint(hashes.SHA256()).hex(":").upper()


def _migrar_version_vieja() -> None:
    """v0.1–0.3 guardaban una sola firma suelta en DATOS; ahora cada firma tiene su carpeta."""
    viejo = DATOS / "certificado.p12"
    if not viejo.exists():
        return
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    cn = x509.load_pem_x509_certificate((DATOS / "certificado.cer").read_bytes()) \
        .subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    carpeta = FIRMAS / _slug(cn)
    carpeta.mkdir(parents=True, exist_ok=True)
    _privado(carpeta, carpeta=True)
    for nombre in ("certificado.p12", "certificado.cer", "firma.png", "anteriores"):
        if (DATOS / nombre).exists():
            shutil.move(DATOS / nombre, carpeta / nombre)
    Perfil(carpeta).guardar_datos(cn, "")


def foto_a_firma(foto: Path, destino: Path) -> None:
    """Foto de la firma en papel blanco → PNG transparente, recortado y en tinta azul oscuro."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(foto)).convert("L")
    img.thumbnail((2000, 2000))
    hist, total, acumulado, papel = img.histogram(), img.width * img.height, 0, 255
    for nivel in range(255, -1, -1):  # el tono del papel: la mitad más clara de la foto
        acumulado += hist[nivel]
        if acumulado >= total * 0.5:
            papel = nivel
            break
    umbral = int(papel * 0.65)
    alfa = img.point(lambda v: 0 if v > umbral else min(255, int((umbral - v) * 255 / max(umbral, 1) * 2.5)))
    caja = alfa.getbbox()
    if not caja:
        raise ValueError("No encontré la firma en la foto (¿papel blanco y tinta oscura?).")
    alfa = alfa.crop(caja)
    tinta = Image.new("RGBA", alfa.size, (20, 35, 90, 0))
    tinta.putalpha(alfa)
    tinta.save(destino)
    _privado(destino)


# =====================================================================
#  Documentos: convertir a PDF y sugerir dónde va la firma
# =====================================================================

def convertir_a_pdf(origen: Path, destino: Path) -> None:
    ext = origen.suffix.lower()
    if ext in EXT_PDF:
        shutil.copy2(origen, destino)
    elif ext in EXT_IMAGEN:
        import pymupdf
        from PIL import Image
        # Imagen en hoja carta con márgenes (si es más ancha que alta, horizontal).
        with Image.open(origen) as im:
            ancho, alto = im.size
        hoja = pymupdf.paper_rect("letter-l" if ancho > alto else "letter")
        doc = pymupdf.open()
        pag = doc.new_page(width=hoja.width, height=hoja.height)
        pag.insert_image(hoja + (36, 36, -36, -36), filename=str(origen), keep_proportion=True)
        doc.save(destino)
    elif ext in EXT_TEXTO:
        import textwrap
        import pymupdf
        lineas = [r for linea in origen.read_text("utf-8", errors="replace").splitlines()
                  for r in (textwrap.wrap(linea, 95) or [""])]
        doc = pymupdf.open()
        hoja = pymupdf.paper_rect("letter")
        por_hoja = 52
        for i in range(0, max(len(lineas), 1), por_hoja):
            pag = doc.new_page(width=hoja.width, height=hoja.height)
            pag.insert_text((60, 72), "\n".join(lineas[i:i + por_hoja]), fontsize=10, fontname="helv")
        doc.save(destino)
    elif ext in EXT_OFFICE:
        _office_a_pdf(origen, destino)
    else:
        raise ValueError(f"No sé firmar archivos {ext}. Formatos: {', '.join(sorted(EXTENSIONES))}")


def _office_a_pdf(origen: Path, destino: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / ("entrada" + origen.suffix.lower())
        shutil.copy2(origen, src)
        if X2T.exists() and X2T_FUENTES.exists():  # convertidor de OnlyOffice
            params = tmp / "p.xml"
            params.write_text(
                '<?xml version="1.0" encoding="utf-8"?><TaskQueueDataConvert>'
                f"<m_sFileFrom>{src}</m_sFileFrom><m_sFileTo>{tmp / 'salida.pdf'}</m_sFileTo>"
                "<m_nFormatTo>513</m_nFormatTo><m_sFontDir>/usr/share/fonts</m_sFontDir>"
                f"<m_sAllFontsPath>{X2T_FUENTES}</m_sAllFontsPath>"
                + f"<m_sThemeDir>{X2T.parent / 'empty'}</m_sThemeDir>"
                "<m_bDontSaveAdditional>true</m_bDontSaveAdditional></TaskQueueDataConvert>", "utf-8")
            subprocess.run([str(X2T), str(params)], capture_output=True, timeout=180)
        elif shutil.which("soffice") or shutil.which("libreoffice"):
            subprocess.run([shutil.which("soffice") or shutil.which("libreoffice"), "--headless",
                            "--convert-to", "pdf", "--outdir", str(tmp), str(src)],
                           capture_output=True, timeout=180)
            (tmp / "entrada.pdf").exists() and (tmp / "entrada.pdf").rename(tmp / "salida.pdf")
        elif X2T.exists():  # OnlyOffice instalado pero nunca abierto: aún no tiene su lista de letras
            raise ValueError("Abre OnlyOffice una vez (y ciérralo) para que prepare sus tipos de letra; "
                             "después ya se pueden firmar Word y Excel.")
        else:
            raise ValueError("Para firmar Word/Excel hace falta OnlyOffice o LibreOffice instalado.")
        if not (tmp / "salida.pdf").exists():
            raise ValueError(f"No se pudo convertir {origen.name} a PDF.")
        shutil.copy2(tmp / "salida.pdf", destino)


def lugar_sugerido(pdf: Path) -> tuple[int, tuple[float, float, float, float] | None]:
    """Página y renglón de firma «_____» (el último del documento), en coordenadas de pantalla
    de PyMuPDF (origen arriba a la izquierda). Si no hay línea: última página, sin renglón."""
    import pymupdf
    with pymupdf.open(pdf) as doc:
        for n in range(len(doc) - 1, -1, -1):
            rects = doc[n].search_for("_____")
            if rects:
                r = max(rects, key=lambda r: (r.y1, r.x0))
                return n, (r.x0, r.y0, r.x1, r.y1)
        return len(doc) - 1, None


# =====================================================================
#  Firmar
# =====================================================================

def leyenda(perfil: Perfil, cuando: dt.datetime) -> str:
    return f"Firmado electrónicamente por {perfil.nombre} · {cuando:%d/%m/%Y %H:%M}"


def componer_sello(perfil: Perfil, cuando: dt.datetime):
    """Dibujo de la firma + leyenda debajo, en UNA imagen (PIL). La vista previa de la ventana
    y el PDF usan esta misma imagen, así lo que se ve al acomodarla es lo que sale."""
    from PIL import Image, ImageDraw, ImageFont

    dibujo = Image.open(perfil.dibujo).convert("RGBA")
    ancho = max(dibujo.width, 600)
    texto = leyenda(perfil, cuando)
    tam = int(ancho * 0.045)
    while True:  # la leyenda cabe a lo ancho de la firma
        letra = ImageFont.truetype(FUENTE, tam) if FUENTE else ImageFont.load_default(tam)
        caja = letra.getbbox(texto)
        if caja[2] - caja[0] <= ancho or tam <= 8:
            break
        tam -= 1
    alto_texto = int(tam * 1.5)
    sello = Image.new("RGBA", (ancho, dibujo.height + alto_texto), (255, 255, 255, 0))
    sello.alpha_composite(dibujo, ((ancho - dibujo.width) // 2, 0))
    d = ImageDraw.Draw(sello)
    d.text((ancho / 2, dibujo.height + alto_texto / 2), texto, font=letra, fill=(80, 80, 80, 255), anchor="mm")
    return sello


def firmar_pdf(pdf: Path, salida: Path, perfil: Perfil, firmante, pagina: int,
               caja: tuple[float, float, float, float], cuando: dt.datetime | None = None) -> Path:
    """Firma (visible + digital) en la `caja` de la `pagina` (coordenadas de pantalla de PyMuPDF).
    Si el PDF ya tenía firmas, la nueva se agrega sin romperlas (actualización incremental)."""
    import pymupdf
    from pyhanko import stamp
    from pyhanko.pdf_utils import images
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign import fields, signers

    if not perfil.dibujo.exists():
        raise ValueError(f"La firma de {perfil.nombre} no tiene dibujo todavía.")
    with pymupdf.open(pdf) as doc:
        r = pymupdf.Rect(caja) * ~doc[pagina].transformation_matrix  # pantalla → espacio PDF
    r.normalize()
    caja_pdf = tuple(round(v) for v in (r.x0, r.y0, r.x1, r.y1))  # pyhanko usa Fraction: puntos enteros
    with open(pdf, "rb") as f:
        existentes = {s.field_name for s in PdfFileReader(f).embedded_signatures}
    base = "Firma-" + _slug(perfil.nombre)
    campo, n = base, 2
    while campo in existentes:
        campo, n = f"{base}-{n}", n + 1

    sello = componer_sello(perfil, cuando or dt.datetime.now())
    estilo = stamp.StaticStampStyle(background=images.PdfImage(sello), background_opacity=1, border_width=0)
    temporal = salida.with_name(salida.name + ".tmp")
    try:
        with open(pdf, "rb") as f:
            w = IncrementalPdfFileWriter(f, strict=False)
            meta = signers.PdfSignatureMetadata(
                field_name=campo, name=perfil.nombre,
                reason=perfil.cargo or "Firma del documento", md_algorithm="sha256")
            pdf_signer = signers.PdfSigner(
                meta, signer=firmante, stamp_style=estilo,
                new_field_spec=fields.SigFieldSpec(campo, on_page=pagina, box=caja_pdf))
            with open(temporal, "wb") as out:
                pdf_signer.sign_pdf(w, output=out)
        os.replace(temporal, salida)
    finally:
        temporal.unlink(missing_ok=True)
    return salida


def nombre_salida(origen: Path) -> Path:
    """doc.docx → doc-firmado.pdf; si ya es «-firmado.pdf» (otra persona firma), se queda igual."""
    if origen.suffix.lower() == ".pdf" and origen.stem.endswith("-firmado"):
        return origen
    return origen.with_name(origen.stem + "-firmado.pdf")


# =====================================================================
#  Verificar
# =====================================================================

def todos_los_certificados() -> list[Path]:
    certs = [c for p in listar_perfiles() for c in p.certificados()]
    certs += sorted((DATOS / "eliminadas").glob("*/certificado.cer"))
    certs += sorted((DATOS / "eliminadas").glob("*/anteriores/*/certificado.cer"))
    return certs


def verificar(pdf: Path) -> tuple[bool, list[dict]]:
    """Revisa TODAS las firmas del PDF. Regresa (todo_bien, [detalle por firma])."""
    from pyhanko.keys import load_cert_from_pemder
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.diff_analysis import ModificationLevel
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    raices = [load_cert_from_pemder(str(c)) for c in todos_los_certificados()]
    ctx = ValidationContext(trust_roots=raices) if raices else None
    detalles = []
    with open(pdf, "rb") as f:
        r = PdfFileReader(f, strict=False)
        for sig in r.embedded_signatures:
            st = validate_pdf_signature(sig, ctx)
            cubre = st.coverage.name if st.coverage else ""
            # Las firmas de antes quedan cubriendo «su» versión; lo que se agregó después solo
            # puede ser otras firmas (FORM_FILLING o menos). Cualquier otra cosa = alterado.
            sin_cambios = st.intact and st.valid and (
                cubre == "ENTIRE_FILE" or (st.modification_level is not None
                                           and st.modification_level <= ModificationLevel.FORM_FILLING))
            fecha = sig.self_reported_timestamp
            detalles.append({
                "quien": sig.signer_cert.subject.native.get("common_name", "?"),
                "cargo": sig.signer_cert.subject.native.get("title", ""),
                "fecha": fecha.astimezone() if fecha else None,
                "sin_cambios": sin_cambios,
                "reconocida": bool(st.trusted),
                "despues_hubo_firmas": cubre != "ENTIRE_FILE",
            })
    return (bool(detalles) and all(d["sin_cambios"] and d["reconocida"] for d in detalles)), detalles


def verificar_texto(pdf: Path) -> tuple[bool, str]:
    ok, detalles = verificar(pdf)
    if not detalles:
        return False, "Este documento no tiene firma electrónica."
    lineas = []
    for d in detalles:
        lineas.append(f"Firmado por: {d['quien']}" + (f" ({d['cargo']})" if d["cargo"] else ""))
        if d["fecha"]:
            lineas.append(f"  Fecha: {d['fecha']:%d/%m/%Y %H:%M}")
        lineas.append("  Documento sin cambios: " + ("SÍ" if d["sin_cambios"] else "NO — fue modificado después de firmar"))
        lineas.append("  Firma registrada en este programa: " + ("SÍ" if d["reconocida"] else "NO (desconocida)"))
    return ok, "\n".join(lineas)


# =====================================================================

def main(argv: list[str]) -> None:
    if not argv or argv[0] == "ventana":
        from firmaelectronica.ventana import abrir
        abrir([Path(p) for p in argv[1:]])
    elif argv[0] == "ventana-verificar" and len(argv) == 2:
        from firmaelectronica.ventana import abrir
        abrir([], verificar=Path(argv[1]))
    elif argv[0] in ("-h", "--help"):
        print(__doc__)
        print(f"{NOMBRE} {__version__} — {CREDITO}")
    elif argv[0] == "--version":
        print(f"{NOMBRE} {__version__} — {CREDITO}")
    elif argv[0] == "verificar" and len(argv) == 2:
        ok, texto = verificar_texto(Path(argv[1]))
        print(texto)
        sys.exit(0 if ok else 1)
    else:
        sys.exit(f"Orden no reconocida. Usa --help. ({NOMBRE} {__version__})")


def run() -> None:
    main(sys.argv[1:])

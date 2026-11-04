"""Firma Electrónica: varias firmas (cada una con su dibujo, su certificado y su contraseña),
para firmar PDF, Word, Excel, OpenDocument, imágenes y texto (se convierten a PDF).

Uso:
    python -m firmaelectronica [ventana [documento ...]]   ventana (lo normal)
    python -m firmaelectronica ventana-verificar doc.pdf   ventana en «Verificar»
    python -m firmaelectronica verificar doc.pdf           revisar firmas desde la terminal
    python -m firmaelectronica --version

Cada firma digital usa un certificado propio (RSA 3072 + SHA-256): si alguien cambia el PDF
después, se ve al verificarlo. No tiene el peso legal de la e.firma del SAT.

Seguridad (v0.6):
- La llave privada y el dibujo de la firma se guardan cifrados con AES-256-GCM y una llave
  sacada de la contraseña con scrypt (128 MB de memoria por intento: adivinar a fuerza bruta
  es muy lento). Antes la llave iba en un .p12 cuya comprobación de contraseña era rápida.
- Sello de tiempo (RFC 3161) de DigiCert o Sectigo: la fecha de la firma la certifica un
  tercero, no el reloj de la PC. Al servicio solo viaja una huella (hash), no el documento.
- Revocar: si roban o pierden la llave, se marca su certificado como revocado desde ese
  momento; lo firmado después (o sin sello de tiempo) ya no sale como válido.
- Las llaves de certificados anteriores o eliminados se destruyen; solo se guarda la parte
  pública (.cer), que es lo único que hace falta para verificar.
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

__version__ = "0.6.0"
CREDITO = "Este programa fue realizado por Genaro Velasco"
NOMBRE = "Firma Electrónica"

DATOS = Path.home() / ".local/share/firma-electronica"
FIRMAS = DATOS / "firmas"
REVOCADOS = DATOS / "revocados.json"
CLAVE_MIN = 12
# Servicios de sello de tiempo gratuitos cuyos certificados ya reconoce el sistema.
TSAS = ("http://timestamp.digicert.com", "http://timestamp.sectigo.com")
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


def _borrar_seguro(ruta: Path) -> None:
    """Sobrescribe y borra. (En btrfs/SSD puede quedar una copia vieja en el disco: la protección
    de fondo es que esos archivos siempre estuvieron cifrados.)"""
    try:
        tam = ruta.stat().st_size
        with open(ruta, "r+b") as f:
            f.write(os.urandom(tam))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    ruta.unlink(missing_ok=True)


# --- cifrado con contraseña: scrypt (lento y con mucha memoria) + AES-256-GCM ---
_MAGIA = b"FIRMA-E1"
_SCRYPT_LOG_N, _SCRYPT_R, _SCRYPT_P = 17, 8, 1   # 2^17 · 8 · 128 B = 128 MB por intento


def _llave_de(clave: str, sal: bytes, log_n: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=sal, length=32, n=2 ** log_n, r=_SCRYPT_R, p=_SCRYPT_P).derive(clave.encode())


def cifrar(datos: bytes, clave: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    sal, nonce = os.urandom(16), os.urandom(12)
    cab = _MAGIA + bytes([_SCRYPT_LOG_N])
    return cab + sal + nonce + AESGCM(_llave_de(clave, sal, _SCRYPT_LOG_N)).encrypt(nonce, datos, cab)


def descifrar(blob: bytes, clave: str) -> bytes | None:
    """Los datos, o None si la contraseña no es la correcta (o el archivo se dañó)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    n = len(_MAGIA)
    if len(blob) < n + 29 or not blob.startswith(_MAGIA):
        return None
    cab, sal, nonce, ct = blob[:n + 1], blob[n + 1:n + 17], blob[n + 17:n + 29], blob[n + 29:]
    try:
        return AESGCM(_llave_de(clave, sal, cab[n])).decrypt(nonce, ct, cab)
    except InvalidTag:
        return None


def _escribir_privado(ruta: Path, datos: bytes) -> None:
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_bytes(datos)
    _privado(tmp)
    os.replace(tmp, ruta)


def _firmante(llave_der: bytes, cert_pem: bytes):
    """SimpleSigner de pyhanko a partir de la llave (PKCS#8 DER) y el certificado."""
    from asn1crypto import keys as a_keys, pem as a_pem, x509 as a_x509
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore
    _, _, der = a_pem.unarmor(cert_pem)
    cert = a_x509.Certificate.load(der)
    return signers.SimpleSigner(signing_cert=cert, signing_key=a_keys.PrivateKeyInfo.load(llave_der),
                                cert_registry=SimpleCertificateStore.from_certs([cert]))


def huella_de(cert_pem_o_der: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    c = (x509.load_pem_x509_certificate(cert_pem_o_der) if cert_pem_o_der.lstrip().startswith(b"-----")
         else x509.load_der_x509_certificate(cert_pem_o_der))
    return c.fingerprint(hashes.SHA256()).hex(":").upper()


def revocados() -> dict:
    try:
        return json.loads(REVOCADOS.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def revocar_certificado(cer: Path, nombre: str, motivo: str, desde: dt.datetime | None = None) -> None:
    datos = revocados()
    datos[huella_de(cer.read_bytes())] = {
        "desde": (desde or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat(),
        "nombre": nombre, "motivo": motivo}
    DATOS.mkdir(parents=True, exist_ok=True)
    _escribir_privado(REVOCADOS, json.dumps(datos, ensure_ascii=False, indent=2).encode())


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
    def p12(self) -> Path: return self.carpeta / "certificado.p12"   # formato viejo (≤ 0.5)
    @property
    def llave(self) -> Path: return self.carpeta / "certificado.llave"
    @property
    def cer(self) -> Path: return self.carpeta / "certificado.cer"
    @property
    def dibujo(self) -> Path: return self.carpeta / "firma.png"        # formato viejo, sin cifrar
    @property
    def dibujo_cifrado(self) -> Path: return self.carpeta / "firma.png.cifrado"
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

    @property
    def tiene_dibujo(self) -> bool: return self.dibujo_cifrado.exists() or self.dibujo.exists()

    @property
    def dibujo_tam(self) -> tuple[int, int] | None:
        """Ancho y alto del dibujo (no es secreto: sirve para acomodar la firma sin la contraseña)."""
        tam = self._datos().get("dibujo")
        if tam:
            return int(tam[0]), int(tam[1])
        if self.dibujo.exists():
            from PIL import Image
            with Image.open(self.dibujo) as im:
                return im.size
        return None

    def guardar_datos(self, nombre: str, cargo: str, **extra) -> None:
        archivo = self.carpeta / "perfil.json"
        datos = {**self._datos(), "nombre": nombre, "cargo": cargo, **extra}
        archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=2), "utf-8")
        _privado(archivo)

    def abrir(self, clave: str):
        """El firmante (llave privada) si la contraseña es la correcta; si no, None.
        Si la firma está en el formato viejo (.p12, dibujo sin cifrar), aquí se pasa al nuevo."""
        if self.llave.exists():
            der = descifrar(self.llave.read_bytes(), clave)
            return _firmante(der, self.cer.read_bytes()) if der else None
        if not self.p12.exists():
            return None
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        try:
            llave, _cert, _otros = pkcs12.load_key_and_certificates(self.p12.read_bytes(), clave.encode())
        except Exception:
            return None
        der = llave.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
        _escribir_privado(self.llave, cifrar(der, clave))
        _borrar_seguro(self.p12)
        self._cifrar_dibujo_viejo(clave)
        return _firmante(der, self.cer.read_bytes())

    def _cifrar_dibujo_viejo(self, clave: str) -> None:
        if self.dibujo.exists() and not self.dibujo_cifrado.exists():
            self.guardar_dibujo(self.dibujo, clave)

    def guardar_dibujo(self, png: Path, clave: str) -> None:
        """Cifra el PNG con la contraseña de esta firma y borra el PNG suelto."""
        from PIL import Image
        with Image.open(png) as im:
            tam = im.size
        _escribir_privado(self.dibujo_cifrado, cifrar(png.read_bytes(), clave))
        self.guardar_datos(self.nombre, self.cargo, dibujo=list(tam))
        _borrar_seguro(png)
        if png != self.dibujo:
            _borrar_seguro(self.dibujo)

    def leer_dibujo(self, clave: str):
        """El dibujo (imagen PIL RGBA), o None si no hay o la contraseña no es la correcta."""
        import io
        from PIL import Image
        if self.dibujo_cifrado.exists():
            datos = descifrar(self.dibujo_cifrado.read_bytes(), clave)
            return Image.open(io.BytesIO(datos)).convert("RGBA") if datos else None
        if self.dibujo.exists():
            return Image.open(self.dibujo).convert("RGBA")
        return None

    def revocado(self) -> dict | None:
        return revocados().get(huella_de(self.cer.read_bytes())) if self.cer.exists() else None

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

    def _jubilar_certificado(self) -> None:
        """El certificado actual pasa a anteriores/ (solo la parte pública) y su llave se destruye."""
        viejo = self.anteriores / dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        viejo.mkdir(parents=True)
        shutil.move(self.cer, viejo / self.cer.name)
        for llave in (self.llave, self.p12):
            _borrar_seguro(llave)

    def renovar(self, clave_actual: str, clave_nueva: str) -> str:
        if len(clave_nueva) < CLAVE_MIN:
            raise ValueError(f"La contraseña nueva debe tener al menos {CLAVE_MIN} caracteres.")
        if self.abrir(clave_actual) is None:
            raise ValueError("La contraseña actual no es correcta.")
        dibujo = self.dibujo_cifrado.exists() and descifrar(self.dibujo_cifrado.read_bytes(), clave_actual)
        self._jubilar_certificado()
        huella = _generar_certificado(self, clave_nueva)
        if dibujo:
            _escribir_privado(self.dibujo_cifrado, cifrar(dibujo, clave_nueva))
        return huella

    def revocar(self, clave_nueva: str, motivo: str, clave_actual: str = "") -> bool:
        """Robo o pérdida: el certificado actual queda REVOCADO desde ahora y se crea uno nuevo.
        No pide la contraseña vieja; si se da (y es correcta) se conserva el dibujo, si no hay que
        volver a dibujarlo. Regresa True si se conservó el dibujo."""
        if len(clave_nueva) < CLAVE_MIN:
            raise ValueError(f"La contraseña nueva debe tener al menos {CLAVE_MIN} caracteres.")
        dibujo = None
        if clave_actual and self.dibujo_cifrado.exists():
            dibujo = descifrar(self.dibujo_cifrado.read_bytes(), clave_actual)
        elif self.dibujo.exists():
            dibujo = self.dibujo.read_bytes()
        revocar_certificado(self.cer, self.nombre, motivo)
        self._jubilar_certificado()
        _borrar_seguro(self.dibujo_cifrado)
        _borrar_seguro(self.dibujo)
        _generar_certificado(self, clave_nueva)
        if dibujo:
            _escribir_privado(self.dibujo_cifrado, cifrar(dibujo, clave_nueva))
        else:
            self.guardar_datos(self.nombre, self.cargo, dibujo=None)
        return bool(dibujo)

    def eliminar(self) -> None:
        """La firma se va a eliminadas/ con solo su parte pública: sus PDF ya firmados se siguen
        verificando, pero ya nadie puede firmar con ella (la llave y el dibujo se destruyen)."""
        destino = DATOS / "eliminadas" / f"{self.carpeta.name}-{dt.datetime.now():%Y%m%d-%H%M%S}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(self.carpeta, destino)
        _destruir_secretos(destino)


def _destruir_secretos(carpeta: Path) -> None:
    for patron in ("certificado.p12", "certificado.llave", "firma.png", "firma.png.cifrado"):
        for archivo in carpeta.rglob(patron):
            _borrar_seguro(archivo)


def listar_perfiles() -> list[Perfil]:
    _migrar_version_vieja()
    _limpiar_llaves_jubiladas()
    if not FIRMAS.is_dir():
        return []
    return sorted((Perfil(c) for c in FIRMAS.iterdir()
                   if (c / "certificado.llave").exists() or (c / "certificado.p12").exists()),
                  key=lambda p: p.nombre.lower())


def _limpiar_llaves_jubiladas() -> None:
    """Versiones ≤ 0.5 guardaban la llave de los certificados anteriores y de las firmas eliminadas.
    No hace falta para verificar (basta el .cer) y es un riesgo: se destruye."""
    for anteriores in FIRMAS.glob("*/anteriores"):
        _destruir_secretos(anteriores)
    if (DATOS / "eliminadas").is_dir():
        _destruir_secretos(DATOS / "eliminadas")


def crear_perfil(nombre: str, cargo: str, clave: str) -> Perfil:
    nombre = " ".join(nombre.split())
    if not nombre:
        raise ValueError("Falta el nombre completo.")
    if len(clave) < CLAVE_MIN:
        raise ValueError(f"La contraseña debe tener al menos {CLAVE_MIN} caracteres.")
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
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if len(clave) < CLAVE_MIN:
        raise ValueError(f"La contraseña debe tener al menos {CLAVE_MIN} caracteres.")
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
    der = llave.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    _escribir_privado(perfil.llave, cifrar(der, clave))
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


def _muestra_dibujo(perfil: Perfil):
    """Recuadro del tamaño exacto del dibujo, para acomodar la firma sin la contraseña
    (el dibujo verdadero está cifrado y solo se abre al firmar)."""
    from PIL import Image, ImageDraw, ImageFont
    ancho, alto = perfil.dibujo_tam or (900, 300)
    img = Image.new("RGBA", (ancho, alto), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    g = max(3, ancho // 150)
    d.rounded_rectangle((g, g, ancho - g, alto - g), radius=alto // 8, outline=(20, 35, 90, 150), width=g,
                        fill=(20, 35, 90, 18))
    texto = f"Firma de {perfil.nombre}"
    tam = max(10, alto // 4)
    while True:
        letra = ImageFont.truetype(FUENTE, tam) if FUENTE else ImageFont.load_default(tam)
        caja = letra.getbbox(texto)
        if caja[2] - caja[0] <= ancho * 0.9 or tam <= 8:
            break
        tam -= 1
    d.text((ancho / 2, alto / 2), texto, font=letra, fill=(20, 35, 90, 200), anchor="mm")
    return img


def componer_sello(perfil: Perfil, cuando: dt.datetime, dibujo=None):
    """Dibujo de la firma + leyenda debajo, en UNA imagen (PIL). Sin `dibujo` (aún no se da la
    contraseña) va un recuadro del mismo tamaño: lo que se acomoda en la vista es lo que sale."""
    from PIL import Image, ImageDraw, ImageFont

    dibujo = dibujo if dibujo is not None else _muestra_dibujo(perfil)
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


class SinSelloDeTiempo(Exception):
    """Ningún servicio de sello de tiempo contestó (¿sin internet?)."""


def firmar_pdf(pdf: Path, salida: Path, perfil: Perfil, firmante, dibujo, pagina: int,
               caja: tuple[float, float, float, float], cuando: dt.datetime | None = None,
               con_sello: bool = True) -> Path:
    """Firma (visible + digital) en la `caja` de la `pagina` (coordenadas de pantalla de PyMuPDF).
    Si el PDF ya tenía firmas, la nueva se agrega sin romperlas (actualización incremental).
    con_sello: pide sello de tiempo a DigiCert/Sectigo; si ninguno contesta → SinSelloDeTiempo."""
    import pymupdf
    from pyhanko import stamp
    from pyhanko.pdf_utils import images
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign import fields, signers, timestamps

    if dibujo is None:
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

    sello = componer_sello(perfil, cuando or dt.datetime.now(), dibujo)
    estilo = stamp.StaticStampStyle(background=images.PdfImage(sello), background_opacity=1, border_width=0)
    temporal = salida.with_name(salida.name + ".tmp")
    servicios = TSAS if con_sello else (None,)
    try:
        for i, url in enumerate(servicios):
            sellador = timestamps.HTTPTimeStamper(url, timeout=15) if url else None
            try:
                with open(pdf, "rb") as f:
                    w = IncrementalPdfFileWriter(f, strict=False)
                    meta = signers.PdfSignatureMetadata(
                        field_name=campo, name=perfil.nombre,
                        reason=perfil.cargo or "Firma del documento", md_algorithm="sha256")
                    pdf_signer = signers.PdfSigner(
                        meta, signer=firmante, stamp_style=estilo, timestamper=sellador,
                        new_field_spec=fields.SigFieldSpec(campo, on_page=pagina, box=caja_pdf))
                    with open(temporal, "wb") as out:
                        pdf_signer.sign_pdf(w, output=out)
                break
            except Exception as e:  # el servicio no contestó: probamos el siguiente
                if url is None or not _es_falla_de_sello(e):
                    raise
                if i == len(servicios) - 1:
                    raise SinSelloDeTiempo(str(e)) from e
        os.replace(temporal, salida)
    finally:
        temporal.unlink(missing_ok=True)
    return salida


def _es_falla_de_sello(e: BaseException) -> bool:
    """¿El error vino de pedir el sello (red, servicio caído), y no de la firma en sí?"""
    import asyncio
    from pyhanko.sign.timestamps import TimestampRequestError
    tipos: tuple = (TimestampRequestError, asyncio.TimeoutError, TimeoutError, OSError)
    try:
        import aiohttp
        tipos += (aiohttp.ClientError,)
    except ImportError:
        pass
    while e is not None:
        if isinstance(e, tipos):
            return True
        e = e.__cause__ or e.__context__
    return False


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
    nuestras = {c.sha256 for c in raices}
    revoc = revocados()
    # Las raíces del sistema hacen falta para comprobar el sello de tiempo (DigiCert/Sectigo);
    # «registrada» se decide aparte: el certificado tiene que ser uno de los de este programa.
    ctx = ValidationContext(extra_trust_roots=raices)
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
            ts = st.timestamp_validity
            sello_ok = bool(ts and ts.intact and ts.valid and ts.trusted)
            fecha_sello = ts.timestamp if sello_ok else None
            fecha = fecha_sello or sig.self_reported_timestamp
            registrada = sig.signer_cert.sha256 in nuestras
            revocada = revoc.get(sig.signer_cert.sha256.hex(":").upper()) if registrada else None
            antes_de_revocar = bool(revocada and fecha_sello
                                    and fecha_sello < dt.datetime.fromisoformat(revocada["desde"]))
            detalles.append({
                "quien": sig.signer_cert.subject.native.get("common_name", "?"),
                "cargo": sig.signer_cert.subject.native.get("title", ""),
                "fecha": fecha.astimezone() if fecha else None,
                "sello": (ts.signing_cert.subject.native.get("organization_name") or "?") if sello_ok else None,
                "sello_invalido": bool(ts) and not sello_ok,
                "sin_cambios": sin_cambios,
                "registrada": registrada,
                "revocada": (dt.datetime.fromisoformat(revocada["desde"]).astimezone() if revocada else None),
                "antes_de_revocar": antes_de_revocar,
                "reconocida": registrada and (not revocada or antes_de_revocar),
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
            origen = (f"sello de tiempo de {d['sello']}: fecha confiable" if d["sello"] else
                      "sello de tiempo NO válido; fecha según el reloj de la PC" if d["sello_invalido"] else
                      "sin sello de tiempo; fecha según el reloj de la PC que firmó")
            lineas.append(f"  Fecha: {d['fecha']:%d/%m/%Y %H:%M} ({origen})")
        lineas.append("  Documento sin cambios: " + ("SÍ" if d["sin_cambios"] else "NO — fue modificado después de firmar"))
        if not d["registrada"]:
            lineas.append("  Firma registrada en este programa: NO (desconocida)")
        elif d["revocada"] and d["antes_de_revocar"]:
            lineas.append(f"  Firma registrada en este programa: SÍ (su certificado se revocó el "
                          f"{d['revocada']:%d/%m/%Y %H:%M}, pero el sello de tiempo prueba que firmó antes)")
        elif d["revocada"]:
            lineas.append(f"  Firma registrada en este programa: REVOCADA desde el {d['revocada']:%d/%m/%Y %H:%M} "
                          "— no se puede confiar en ella" + ("" if d["sello"] else
                                                             " (sin sello de tiempo no se sabe si firmó antes)"))
        else:
            lineas.append("  Firma registrada en este programa: SÍ")
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

"""Ventana de Firma Electrónica.

Pestañas: 1) Firmar documento (vista de la hoja: la firma se arrastra y se agranda/achica),
2) Firmas (varias personas, cada una con su dibujo, certificado y contraseña), 3) Verificar,
4) Archivo (los documentos firmados, por folio, para compararlos con la hoja impresa).
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QStandardPaths
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PIL.ImageQt import ImageQt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy, QSlider, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from firmaelectronica import estilo, nucleo

TINTA = QColor(20, 35, 90)
GROSOR = 3.2           # grosor del trazo en pantalla
ESCALA_PNG = 3         # el dibujo se guarda 3 veces más grande que el recuadro (imprime nítido)
ICONO = Path(__file__).with_name("icono.png")
VERDE_OK = estilo.OK
ROJO = estilo.MAL
MORADO = QColor(estilo.ACENTO)
FILTRO_DOCS = ("Documentos (" + " ".join("*" + e for e in sorted(nucleo.EXTENSIONES)) + ")"
               ";;PDF (*.pdf);;Word / OpenDocument (*.docx *.doc *.odt *.rtf)"
               ";;Excel (*.xlsx *.xls *.ods);;Imágenes (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp)")


def _etiqueta(texto: str, nombre: str = "", ajustar: bool = True) -> QLabel:
    lbl = QLabel(texto)
    if nombre:
        lbl.setObjectName(nombre)
    lbl.setWordWrap(ajustar)
    return lbl


def _titulo(texto: str, sub: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(2)
    lay.addWidget(_etiqueta(texto, "subtitulo"))
    lay.addWidget(_etiqueta(sub, "suave"))
    return w


def _tarjeta() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("tarjeta")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    return f, lay


def _boton_grande(texto: str) -> QPushButton:
    b = QPushButton(texto)
    b.setObjectName("principal")
    return b


def _descargas() -> str:
    return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)


def pedir_clave(padre: QWidget, perfil: nucleo.Perfil, para: str):
    """Pide la contraseña de esa firma hasta que sea correcta o se cancele.
    Regresa (firmante, contraseña) o None."""
    texto = f"Contraseña de la firma de <b>{perfil.nombre}</b> para {para}:"
    while True:
        clave, ok = QInputDialog.getText(padre, nucleo.NOMBRE, texto, QLineEdit.EchoMode.Password)
        if not ok:
            return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        firmante = perfil.abrir(clave)
        QApplication.restoreOverrideCursor()
        if firmante is not None:
            return firmante, clave
        texto = f"<span style='color:{ROJO}'>Contraseña incorrecta.</span> Inténtalo otra vez ({perfil.nombre}):"


# =====================================================================
#  Recuadro para dibujar la firma
# =====================================================================

class PadFirma(QWidget):
    """Recuadro blanco para firmar con mouse, touchpad, tableta o pantalla táctil."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(560, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.trazos: list[list[QPointF]] = []

    def borrar(self) -> None:
        self.trazos.clear()
        self.update()

    def vacio(self) -> bool:
        return not self.trazos

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.trazos.append([e.position()])
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.MouseButton.LeftButton and self.trazos:
            self.trazos[-1].append(e.position())
            self.update()

    @staticmethod
    def _camino(puntos: list[QPointF], escala: float = 1, desplazar: QPointF = QPointF()) -> QPainterPath:
        pts = [(p - desplazar) * escala for p in puntos]
        camino = QPainterPath(pts[0])
        if len(pts) == 1:  # un punto: lo dibujamos como un trazo mínimo
            camino.lineTo(pts[0] + QPointF(0.5, 0.5))
            return camino
        for a, b in zip(pts[1:-1], pts[2:]):  # curvas suaves pasando por los puntos medios
            camino.quadTo(a, (a + b) / 2)
        camino.lineTo(pts[-1])
        return camino

    @staticmethod
    def _pluma(escala: float = 1) -> QPen:
        return QPen(TINTA, GROSOR * escala, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), Qt.GlobalColor.white)
        base = self.height() - 45
        p.setPen(QPen(QColor(170, 170, 170), 1, Qt.PenStyle.DashLine))
        p.drawLine(30, base, self.width() - 30, base)
        p.setPen(QColor(150, 150, 150))
        p.setFont(QFont(self.font().family(), 9))
        p.drawText(30, base + 20, "Firme sobre la línea")
        p.setPen(self._pluma())
        for t in self.trazos:
            p.drawPath(self._camino(t))

    def a_png(self, destino: Path) -> None:
        """Guarda lo dibujado como PNG transparente, recortado a la firma."""
        caja = QRectF()
        for t in self.trazos:
            caja = caja.united(self._camino(t).boundingRect())
        m = GROSOR * 2
        caja = caja.adjusted(-m, -m, m, m)
        img = QImage(int(caja.width() * ESCALA_PNG) + 1, int(caja.height() * ESCALA_PNG) + 1,
                     QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(self._pluma(ESCALA_PNG))
        for t in self.trazos:
            p.drawPath(self._camino(t, ESCALA_PNG, caja.topLeft()))
        p.end()
        img.save(str(destino), "PNG")


class EditorDibujo(QWidget):
    """Pad + «Borrar» + «Usar foto…». `guardar(destino)` deja el PNG (del dibujo o de la foto)."""

    def __init__(self) -> None:
        super().__init__()
        self.pad = PadFirma()
        self.foto: Path | None = None
        self.lbl_foto = QLabel()
        self.lbl_foto.setStyleSheet(f"color:{VERDE_OK}")
        marco = QFrame()
        marco.setFrameShape(QFrame.Shape.StyledPanel)
        lm = QVBoxLayout(marco)
        lm.setContentsMargins(1, 1, 1, 1)
        lm.addWidget(self.pad)
        b_borrar = QPushButton("Borrar")
        b_borrar.clicked.connect(self.borrar)
        b_foto = QPushButton("Mejor usar una foto de mi firma…")
        b_foto.clicked.connect(self.elegir_foto)
        fila = QHBoxLayout()
        fila.addWidget(b_borrar)
        fila.addWidget(self.lbl_foto, 1)
        fila.addWidget(b_foto)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(marco, 1)
        lay.addLayout(fila)

    def borrar(self) -> None:
        self.pad.borrar()
        self.foto = None
        self.lbl_foto.clear()

    def elegir_foto(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Foto de la firma (hoja blanca, tinta oscura)", _descargas(),
                                              "Imágenes (*.jpg *.jpeg *.png *.webp)")
        if ruta:
            self.pad.borrar()
            self.foto = Path(ruta)
            self.lbl_foto.setText(f"✔ Se usará la foto {self.foto.name}")

    def listo(self) -> bool:
        return self.foto is not None or not self.pad.vacio()

    def guardar(self, destino: Path) -> None:
        if self.foto:
            nucleo.foto_a_firma(self.foto, destino)
        else:
            self.pad.a_png(destino)
        destino.chmod(0o600)

    def guardar_en(self, perfil: nucleo.Perfil, clave: str) -> None:
        """Guarda el dibujo CIFRADO en la firma (el PNG pasa un instante por /dev/shm, en memoria)."""
        base = "/dev/shm" if Path("/dev/shm").is_dir() else None
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            png = Path(tmp) / "firma.png"
            self.guardar(png)
            perfil.guardar_dibujo(png, clave)


class DialogoNuevaFirma(QDialog):
    def __init__(self, padre: QWidget) -> None:
        super().__init__(padre)
        self.setWindowTitle("Nueva firma")
        self.resize(640, 560)
        self.perfil: nucleo.Perfil | None = None
        self.txt_nombre = QLineEdit()
        self.txt_nombre.setPlaceholderText("Nombre completo, como debe aparecer")
        self.txt_cargo = QLineEdit()
        self.txt_cargo.setPlaceholderText("Opcional: Director, Supervisor, Enfermera…")
        self.txt_c1, self.txt_c2 = QLineEdit(), QLineEdit()
        for c in (self.txt_c1, self.txt_c2):
            c.setEchoMode(QLineEdit.EchoMode.Password)
            c.setPlaceholderText(f"mínimo {nucleo.CLAVE_MIN} caracteres (puede ser una frase)")
        form = QFormLayout()
        form.addRow("Nombre completo:", self.txt_nombre)
        form.addRow("Cargo:", self.txt_cargo)
        form.addRow("Contraseña:", self.txt_c1)
        form.addRow("Repite la contraseña:", self.txt_c2)
        self.editor = EditorDibujo()
        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botones.button(QDialogButtonBox.StandardButton.Ok).setText("Crear firma")
        botones.button(QDialogButtonBox.StandardButton.Ok).setObjectName("principal")
        botones.accepted.connect(self.crear)
        botones.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(_titulo("Nueva firma", "Cada persona tiene su propia firma y su propia contraseña. "
                                             "La contraseña NO se puede recuperar: anótala en un lugar seguro."))
        lay.addLayout(form)
        lay.addWidget(QLabel("<b>Dibuja la firma:</b>"))
        lay.addWidget(self.editor, 1)
        lay.addWidget(botones)

    def crear(self) -> None:
        if self.txt_c1.text() != self.txt_c2.text():
            QMessageBox.warning(self, nucleo.NOMBRE, "Las contraseñas no coinciden.")
            return
        if not self.editor.listo():
            QMessageBox.warning(self, nucleo.NOMBRE, "Falta dibujar la firma (o elegir una foto).")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            perfil = nucleo.crear_perfil(self.txt_nombre.text(), self.txt_cargo.text(), self.txt_c1.text())
            try:
                self.editor.guardar_en(perfil, self.txt_c1.text())
            except Exception:
                perfil.eliminar()
                raise
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, nucleo.NOMBRE, str(e))
            return
        QApplication.restoreOverrideCursor()
        self.perfil = perfil
        self.accept()


class DialogoDibujo(QDialog):
    def __init__(self, padre: QWidget, perfil: nucleo.Perfil) -> None:
        super().__init__(padre)
        self.setWindowTitle(f"Cambiar dibujo — {perfil.nombre}")
        self.resize(640, 380)
        self.editor = EditorDibujo()
        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        botones.accepted.connect(lambda: self.accept() if self.editor.listo() else
                                 QMessageBox.warning(self, nucleo.NOMBRE, "Primero dibuja la firma."))
        botones.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Nueva firma dibujada de {perfil.nombre}:</b>"))
        lay.addWidget(self.editor, 1)
        lay.addWidget(botones)


# =====================================================================
#  Vista del documento con la firma movible
# =====================================================================

class FirmaItem(QGraphicsItem):
    """La firma sobre la hoja (dibujo + leyenda, la misma imagen que va al PDF): se arrastra con
    el mouse y se agranda/achica desde la esquina morada o con la rueda. Coordenadas en puntos PDF."""

    ESQUINA = 10

    def __init__(self, sello: QPixmap, parte_dibujo: float, ancho: float) -> None:
        super().__init__()
        self.sello = sello
        self.parte_dibujo = parte_dibujo  # fracción de arriba que es el dibujo (abajo va la leyenda)
        self.ancho = ancho
        self.redim = False
        self.al_cambiar = None
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(10)

    def alto(self) -> float:
        return self.ancho * self.sello.height() / max(self.sello.width(), 1)

    def boundingRect(self) -> QRectF:
        return QRectF(-1, -1, self.ancho + 2, self.alto() + 2)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(0, 0, self.ancho, self.alto()), self.sello, QRectF(self.sello.rect()))
        p.setPen(QPen(MORADO, 0.8, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0, 0, self.ancho, self.alto()))
        p.setBrush(MORADO)
        p.setPen(Qt.PenStyle.NoPen)
        e = self.ESQUINA / 2
        p.drawRect(QRectF(self.ancho - e, self.alto() - e, e, e))

    def _en_esquina(self, pos: QPointF) -> bool:
        return pos.x() > self.ancho - self.ESQUINA and pos.y() > self.alto() - self.ESQUINA

    def hoverMoveEvent(self, e) -> None:
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._en_esquina(e.pos()) else Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, e) -> None:
        self.redim = self._en_esquina(e.pos())
        super().mousePressEvent(e) if not self.redim else e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self.redim:
            self.cambiar_ancho(e.pos().x())
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self.redim = False
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e) -> None:
        self.cambiar_ancho(self.ancho * (1.08 if e.delta() > 0 else 1 / 1.08))
        e.accept()

    def cambiar_ancho(self, ancho: float) -> None:
        self.prepareGeometryChange()
        self.ancho = max(50.0, min(400.0, ancho))
        self.update()
        if self.al_cambiar:
            self.al_cambiar(self.ancho)

    def caja(self) -> tuple[float, float, float, float]:
        p = self.pos()
        return (p.x(), p.y(), p.x() + self.ancho, p.y() + self.alto())


class VistaDocumento(QGraphicsView):
    ZOOM = 2.0  # resolución de la hoja en pantalla (pixeles por punto)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(estilo.MESA))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hoja: QGraphicsPixmapItem | None = None
        self.firma: FirmaItem | None = None

    def mostrar_pagina(self, pdf: Path, n: int) -> None:
        import pymupdf
        with pymupdf.open(pdf) as doc:
            pm = doc[n].get_pixmap(matrix=pymupdf.Matrix(self.ZOOM, self.ZOOM), alpha=False)
            img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy()
        if self.hoja:
            self.scene().removeItem(self.hoja)
        self.hoja = self.scene().addPixmap(QPixmap.fromImage(img))
        self.hoja.setScale(1 / self.ZOOM)
        self.hoja.setZValue(0)
        r = self.hoja.sceneBoundingRect()
        self.scene().setSceneRect(r.adjusted(-20, -20, 20, 20))
        self.ajustar()

    def poner_firma(self, item: FirmaItem | None) -> None:
        if self.firma:
            self.scene().removeItem(self.firma)
        self.firma = item
        if item:
            self.scene().addItem(item)

    def ajustar(self) -> None:
        if self.hoja:
            self.fitInView(self.hoja.sceneBoundingRect().adjusted(-10, -10, 10, 10),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.ajustar()

    def limpiar(self) -> None:
        self.scene().clear()
        self.hoja = self.firma = None


# =====================================================================
#  Ventana principal
# =====================================================================

class Ventana(QWidget):
    def __init__(self, docs: list[Path]) -> None:
        super().__init__()
        self.setWindowTitle(f"{nucleo.NOMBRE} {nucleo.__version__}")
        if ICONO.exists():
            self.setWindowIcon(QIcon(str(ICONO)))
        self.resize(1180, 860)
        self._tmp = tempfile.TemporaryDirectory(prefix="firma-")
        self.origen: Path | None = None   # el archivo que eligió la persona
        self.pdf: Path | None = None      # su versión en PDF (la misma si ya era PDF)
        self.pagina = 0
        self.paginas = 0

        # Encabezado al estilo de GeniusIQ: título grande en morado y el ícono a la derecha.
        encabezado = QHBoxLayout()
        textos = QVBoxLayout()
        textos.addWidget(_etiqueta(nucleo.NOMBRE, "titulo"))
        textos.addWidget(_etiqueta("Firma documentos con tu firma dibujada y tu firma electrónica. "
                                   "Cada persona tiene la suya, protegida con su contraseña.", "suave"))
        encabezado.addLayout(textos, 1)
        if ICONO.exists():
            logo = QLabel()
            logo.setPixmap(QPixmap(str(ICONO)).scaled(130, 130, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            encabezado.addWidget(logo, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        # Menú lateral + páginas (self.tabs es la pila de páginas).
        self.menu = QListWidget()
        self.menu.setObjectName("menu")
        self.menu.setFixedWidth(230)
        self.tabs = QStackedWidget()
        for texto, pagina in (("✍   Firmar documento", self._tab_firmar()), ("👤   Firmas", self._tab_firmas()),
                              ("✔   Verificar", self._tab_verificar()), ("🗂   Archivo", self._tab_archivo())):
            self.menu.addItem(texto)
            self.tabs.addWidget(pagina)
        self.menu.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda i: self.menu.currentRow() != i and self.menu.setCurrentRow(i))
        self.menu.setCurrentRow(0)
        cuerpo = QHBoxLayout()
        cuerpo.setSpacing(16)
        cuerpo.addWidget(self.menu)
        cuerpo.addWidget(self.tabs, 1)

        credito = _etiqueta(f"{nucleo.CREDITO} · versión {nucleo.__version__}", "credito")
        credito.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 10)
        lay.setSpacing(14)
        lay.addLayout(encabezado)
        lay.addLayout(cuerpo, 1)
        lay.addWidget(credito)

        self.recargar_firmas()
        if not nucleo.listar_perfiles():
            self.tabs.setCurrentIndex(1)
        docs = [d for d in docs if d.suffix.lower() in nucleo.EXTENSIONES]
        if docs:
            self.cargar_documento(docs[0])

    # ---------------- 1. firmar ----------------
    def _tab_firmar(self) -> QWidget:
        w = QWidget()
        self.lbl_doc = QLabel("<i>Ningún documento abierto</i>")
        b_abrir = QPushButton("Abrir documento…")
        b_abrir.clicked.connect(self.elegir_documento)
        self.cmb_firma = QComboBox()
        self.cmb_firma.setMinimumWidth(260)
        self.cmb_firma.currentIndexChanged.connect(lambda _i: self.colocar_firma(conservar=True))
        fila1 = QHBoxLayout()
        fila1.addWidget(b_abrir)
        fila1.addWidget(self.lbl_doc, 1)
        fila1.addWidget(QLabel("Firma de:"))
        fila1.addWidget(self.cmb_firma)

        self.b_ant = QPushButton("◀")
        self.b_sig = QPushButton("▶")
        self.lbl_pag = QLabel()
        self.b_ant.clicked.connect(lambda: self.ir_a_pagina(self.pagina - 1))
        self.b_sig.clicked.connect(lambda: self.ir_a_pagina(self.pagina + 1))
        self.sld_tam = QSlider(Qt.Orientation.Horizontal)
        self.sld_tam.setRange(50, 400)
        self.sld_tam.setValue(170)
        self.sld_tam.setMaximumWidth(220)
        self.sld_tam.valueChanged.connect(lambda v: self.vista.firma and abs(self.vista.firma.ancho - v) > 0.5
                                          and self.vista.firma.cambiar_ancho(v))
        b_sugerido = QPushButton("Regresar a la línea de firma")
        b_sugerido.clicked.connect(lambda: self.colocar_firma(conservar=False))
        fila2 = QHBoxLayout()
        fila2.addWidget(QLabel("Página:"))
        fila2.addWidget(self.b_ant)
        fila2.addWidget(self.lbl_pag)
        fila2.addWidget(self.b_sig)
        fila2.addSpacing(20)
        fila2.addWidget(QLabel("Tamaño de la firma:"))
        fila2.addWidget(self.sld_tam)
        fila2.addWidget(b_sugerido)
        fila2.addStretch()

        self.vista = VistaDocumento()
        ayuda = _etiqueta("Arrastra la firma con el mouse para acomodarla. Para agrandarla o achicarla: "
                          "la barra «Tamaño», la esquina morada o la rueda del mouse encima de la firma.", "suave")
        self.b_firmar = _boton_grande("✍  Firmar documento")
        self.b_firmar.clicked.connect(self.firmar)
        fila3 = QHBoxLayout()
        fila3.addWidget(ayuda, 1)
        fila3.addWidget(self.b_firmar)

        barra, lb = _tarjeta()
        lb.addLayout(fila1)
        lb.addLayout(fila2)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(barra)
        lay.addWidget(self.vista, 1)
        lay.addLayout(fila3)
        return w

    def perfil_actual(self) -> nucleo.Perfil | None:
        carpeta = self.cmb_firma.currentData()
        return nucleo.Perfil(Path(carpeta)) if carpeta else None

    def elegir_documento(self) -> None:
        inicio = str(self.origen.parent) if self.origen else _descargas()
        ruta, _ = QFileDialog.getOpenFileName(self, "Documento para firmar", inicio, FILTRO_DOCS)
        if ruta:
            self.cargar_documento(Path(ruta))

    def cargar_documento(self, origen: Path) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if origen.suffix.lower() == ".pdf":
                pdf = origen
            else:
                pdf = Path(self._tmp.name) / (origen.stem + ".pdf")
                nucleo.convertir_a_pdf(origen, pdf)
            import pymupdf
            with pymupdf.open(pdf) as doc:
                paginas = len(doc)
            pagina, _ = nucleo.lugar_sugerido(pdf)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, nucleo.NOMBRE, f"No se pudo abrir {origen.name}:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.origen, self.pdf, self.paginas = origen, pdf, paginas
        firmas = len(nucleo.verificar(pdf)[1]) if pdf.suffix.lower() == ".pdf" else 0
        extra = f" — ya tiene {firmas} firma(s)" if firmas else ""
        convertido = "" if origen == pdf else " (se convierte a PDF para firmarlo)"
        self.lbl_doc.setText(f"<b>{origen.name}</b>{convertido}{extra}")
        self.tabs.setCurrentIndex(0)
        self.ir_a_pagina(pagina, colocar=False)
        self.colocar_firma(conservar=False)

    def ir_a_pagina(self, n: int, colocar: bool = True) -> None:
        if not self.pdf or not (0 <= n < self.paginas):
            return
        firma = self.vista.firma
        pos = firma.pos() if firma else None
        self.pagina = n
        self.vista.firma = None  # la conservamos al cambiar de hoja
        if firma:
            self.vista.scene().removeItem(firma)
        self.vista.mostrar_pagina(self.pdf, n)
        if firma:
            self.vista.poner_firma(firma)
            firma.setPos(pos)
        self.lbl_pag.setText(f"{n + 1} de {self.paginas}")
        self.b_ant.setEnabled(n > 0)
        self.b_sig.setEnabled(n < self.paginas - 1)
        self.refrescar_boton()

    def colocar_firma(self, conservar: bool) -> None:
        """Pone (o cambia) la firma en la hoja. conservar=True: mismo lugar y tamaño (cambió la persona)."""
        perfil = self.perfil_actual()
        if not self.pdf or not perfil or not perfil.tiene_dibujo:
            self.vista.poner_firma(None)
            self.refrescar_boton()
            return
        anterior = self.vista.firma
        # El dibujo está cifrado: aquí va un recuadro de su mismo tamaño; el real se pone al firmar.
        sello = nucleo.componer_sello(perfil, dt.datetime.now())
        dibujo_alto = (perfil.dibujo_tam or (900, 300))[1]
        item = FirmaItem(QPixmap.fromImage(ImageQt(sello)), dibujo_alto / sello.height,
                         anterior.ancho if anterior and conservar else 240)
        item.al_cambiar = lambda a: self.sld_tam.setValue(int(a))
        if anterior and conservar:
            item.setPos(anterior.pos())
        else:
            pagina, linea = nucleo.lugar_sugerido(self.pdf)
            if pagina != self.pagina:
                self.vista.poner_firma(None)
                self.ir_a_pagina(pagina, colocar=False)
            hoja = self.vista.hoja.sceneBoundingRect()
            if linea:  # centrada sobre la línea: dibujo y leyenda arriba, sin tapar el nombre de abajo
                x0, _y0, x1, y1 = linea
                item.ancho = max(200.0, min(300.0, (x1 - x0) * 1.5))   # lleva el QR a la derecha
                item.setPos((x0 + x1) / 2 - item.ancho / 2, y1 - item.alto() - 1)
            else:  # sin línea: abajo a la derecha
                item.setPos(hoja.right() - item.ancho - 60, hoja.bottom() - item.alto() - 70)
        self.vista.poner_firma(item)
        self.sld_tam.setValue(int(item.ancho))
        self.refrescar_boton()

    def refrescar_boton(self) -> None:
        perfil = self.perfil_actual()
        listo = bool(self.pdf and perfil and perfil.tiene_dibujo and self.vista.firma)
        self.b_firmar.setEnabled(listo)
        if perfil and not perfil.tiene_dibujo:
            self.b_firmar.setToolTip("Esta firma no tiene dibujo: agrégalo en la pestaña «Firmas».")
        elif not self.pdf:
            self.b_firmar.setToolTip("Primero abre un documento.")
        else:
            self.b_firmar.setToolTip("")

    def firmar(self) -> None:
        perfil = self.perfil_actual()
        if not (self.pdf and perfil and self.vista.firma):
            return
        salida = nucleo.nombre_salida(self.origen)
        if salida.exists() and salida != self.origen and QMessageBox.question(
                self, nucleo.NOMBRE, f"Ya existe {salida.name}. ¿Reemplazarlo?") != QMessageBox.StandardButton.Yes:
            return
        abierta = pedir_clave(self, perfil, "firmar")
        if abierta is None:
            return
        firmante, clave = abierta
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        con_sello = True
        try:
            dibujo = perfil.leer_dibujo(clave)
            while True:
                try:
                    _, folio = nucleo.firmar_pdf(self.pdf, salida, perfil, firmante, dibujo, self.pagina,
                                                 self.vista.firma.caja(), con_sello=con_sello,
                                                 documento=self.origen.name)
                    break
                except nucleo.SinSelloDeTiempo:
                    QApplication.restoreOverrideCursor()
                    r = QMessageBox.question(
                        self, nucleo.NOMBRE,
                        "No se pudo pedir el <b>sello de tiempo</b> (¿sin internet?).<br><br>"
                        "Sin sello, la fecha de la firma es la del reloj de esta PC y no la certifica nadie; "
                        "si algún día se revoca esta firma, este documento ya no saldría como válido.<br><br>"
                        "¿Firmar de todos modos <b>sin sello de tiempo</b>?")
                    if r != QMessageBox.StandardButton.Yes:
                        return
                    con_sello = False
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, nucleo.NOMBRE, f"No se pudo firmar:\n{e}")
            return
        QApplication.restoreOverrideCursor()

        caja = QMessageBox(QMessageBox.Icon.Information, nucleo.NOMBRE,
                           f"Listo, firmado por {perfil.nombre}"
                           + (" (con sello de tiempo)" if con_sello else " (SIN sello de tiempo)")
                           + f":\n\n{salida}\n\nFolio: {folio} (una copia quedó en el Archivo)", parent=self)
        b_abrir = caja.addButton("Abrir", QMessageBox.ButtonRole.AcceptRole)
        b_otra = caja.addButton("Que firme otra persona", QMessageBox.ButtonRole.ActionRole)
        caja.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
        caja.exec()
        if caja.clickedButton() is b_abrir:
            subprocess.Popen(["xdg-open", str(salida)], start_new_session=True)
        self.recargar_archivo(seleccionar=folio)
        # Seguimos con el documento ya firmado: así otra persona puede firmar el mismo PDF.
        self.cargar_documento(salida)
        if caja.clickedButton() is b_otra and self.cmb_firma.count() > 1:
            self.cmb_firma.setCurrentIndex((self.cmb_firma.currentIndex() + 1) % self.cmb_firma.count())

    # ---------------- 2. firmas ----------------
    def _tab_firmas(self) -> QWidget:
        w = QWidget()
        self.lst_firmas = QListWidget()
        self.lst_firmas.setWordWrap(True)
        self.lst_firmas.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        izq_ancho = 320
        self.lst_firmas.currentRowChanged.connect(lambda _r: self.mostrar_firma())
        b_nueva = _boton_grande("＋ Nueva firma")
        b_nueva.clicked.connect(self.nueva_firma)
        izq = QWidget()
        li = QVBoxLayout(izq)
        li.setContentsMargins(0, 0, 0, 0)
        li.addWidget(self.lst_firmas, 1)
        li.addWidget(b_nueva)

        self.lbl_detalle = QLabel()
        self.lbl_detalle.setWordWrap(True)
        self.lbl_detalle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.img_firma = QLabel()
        self.img_firma.setMinimumHeight(130)
        self.img_firma.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_firma.setStyleSheet("background: white; border-radius: 8px")
        b_ver = QPushButton("Ver dibujo…")
        b_ver.clicked.connect(self.ver_dibujo)
        b_dibujo = QPushButton("Cambiar dibujo…")
        b_dibujo.clicked.connect(self.cambiar_dibujo)
        b_renovar = QPushButton("Cambiar contraseña / renovar…")
        b_renovar.clicked.connect(self.renovar)
        b_cer = QPushButton("Guardar certificado público (.cer)…")
        b_cer.clicked.connect(self.exportar_cer)
        b_revocar = QPushButton("Revocar (robo o pérdida)…")
        b_revocar.setObjectName("peligro")
        b_revocar.clicked.connect(self.revocar)
        b_borrar = QPushButton("Eliminar firma…")
        b_borrar.setObjectName("peligro")
        b_borrar.clicked.connect(self.eliminar_firma)
        self.botones_firma = [b_ver, b_dibujo, b_renovar, b_cer, b_revocar, b_borrar]
        fila, fila2 = QHBoxLayout(), QHBoxLayout()
        for b in self.botones_firma[:4]:
            fila.addWidget(b)
        for b in self.botones_firma[4:]:
            fila2.addWidget(b)
        fila2.addStretch()
        der, ld = _tarjeta()
        ld.addWidget(self.lbl_detalle)
        ld.addWidget(self.img_firma)
        ld.addLayout(fila)
        ld.addLayout(fila2)
        ld.addStretch()

        partir = QSplitter()
        partir.addWidget(izq)
        partir.addWidget(der)
        izq.setMinimumWidth(izq_ancho)
        partir.setSizes([izq_ancho, 700])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_titulo("Firmas", "Cada persona tiene su firma: su dibujo y su firma electrónica "
                                        "(certificado) con su propia contraseña. Para firmar o cambiar el "
                                        "dibujo siempre se pide la contraseña de esa persona."))
        lay.addWidget(partir, 1)
        return w

    def recargar_firmas(self, seleccionar: Path | None = None) -> None:
        perfiles = nucleo.listar_perfiles()
        actual = seleccionar or (self.perfil_actual().carpeta if self.perfil_actual() else None)
        self.cmb_firma.blockSignals(True)
        self.cmb_firma.clear()
        self.lst_firmas.clear()
        for p in perfiles:
            etiqueta = p.nombre + (f" — {p.cargo}" if p.cargo else "") + ("" if p.tiene_dibujo else " (sin dibujo)")
            self.cmb_firma.addItem(etiqueta, str(p.carpeta))
            it = QListWidgetItem(etiqueta)
            it.setData(Qt.ItemDataRole.UserRole, str(p.carpeta))
            self.lst_firmas.addItem(it)
        if actual:
            i = self.cmb_firma.findData(str(actual))
            if i >= 0:
                self.cmb_firma.setCurrentIndex(i)
                self.lst_firmas.setCurrentRow(i)
        if self.lst_firmas.currentRow() < 0 and perfiles:
            self.lst_firmas.setCurrentRow(0)
        self.cmb_firma.blockSignals(False)
        self.mostrar_firma()
        self.colocar_firma(conservar=True)

    def firma_seleccionada(self) -> nucleo.Perfil | None:
        it = self.lst_firmas.currentItem()
        return nucleo.Perfil(Path(it.data(Qt.ItemDataRole.UserRole))) if it else None

    def mostrar_firma(self) -> None:
        p = self.firma_seleccionada()
        for b in self.botones_firma:
            b.setEnabled(p is not None)
        if not p:
            self.lbl_detalle.setText("<span style='color:gray'>Todavía no hay firmas. "
                                     "Crea la primera con «＋ Nueva firma».</span>")
            self.img_firma.clear()
            return
        info = p.info()
        anteriores = len(p.certificados()) - 1
        self.lbl_detalle.setText(
            f"<h3 style='margin:0'>{p.nombre}</h3>" + (f"{p.cargo}<br>" if p.cargo else "")
            + (f"Firma electrónica válida del {info['desde']:%d/%m/%Y} al {info['hasta']:%d/%m/%Y}<br>"
               f"<small>Huella: {info['huella']}</small>" if info else "")
            + (f"<br><small>{anteriores} certificado(s) anterior(es) guardado(s) (solo la parte pública).</small>"
               if anteriores else "")
            + ("" if p.llave.exists() else f"<br><small style='color:{ROJO}'>Formato viejo (menos seguro): "
               "se actualiza solo la próxima vez que pongas la contraseña.</small>"))
        self.img_firma.setPixmap(QPixmap())
        if p.dibujo_cifrado.exists():
            self.img_firma.setText("🔒 El dibujo está protegido con la contraseña.<br>"
                                   "<small>Usa «Ver dibujo…» para verlo.</small>")
        elif p.dibujo.exists():
            self.img_firma.setText("El dibujo todavía no está cifrado: se cifra la próxima vez que pongas "
                                   "la contraseña (al firmar o con «Ver dibujo…»).")
        else:
            self.img_firma.setText(f"<span style='color:{ROJO}'>Sin dibujo: usa «Cambiar dibujo…»</span>")

    def ver_dibujo(self) -> None:
        p = self.firma_seleccionada()
        abierta = p and pedir_clave(self, p, "ver su dibujo")
        if not abierta:
            return
        img = p.leer_dibujo(abierta[1])
        self.mostrar_firma()
        if img is None:
            return
        pm = QPixmap.fromImage(ImageQt(img))
        self.img_firma.setPixmap(pm.scaled(520, 115, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))

    def nueva_firma(self) -> None:
        d = DialogoNuevaFirma(self)
        if d.exec() and d.perfil:
            self.recargar_firmas(seleccionar=d.perfil.carpeta)
            QMessageBox.information(self, nucleo.NOMBRE, f"Firma de {d.perfil.nombre} creada.\n\n"
                                    "No olvides la contraseña: no se puede recuperar.")

    def cambiar_dibujo(self) -> None:
        p = self.firma_seleccionada()
        abierta = p and pedir_clave(self, p, "cambiar su dibujo")
        if not abierta:
            return
        d = DialogoDibujo(self, p)
        if d.exec():
            try:
                d.editor.guardar_en(p, abierta[1])
            except Exception as e:
                QMessageBox.warning(self, nucleo.NOMBRE, str(e))
                return
            self.recargar_firmas(seleccionar=p.carpeta)

    def renovar(self) -> None:
        p = self.firma_seleccionada()
        if not p:
            return
        actual, ok = QInputDialog.getText(self, nucleo.NOMBRE, f"Contraseña ACTUAL de {p.nombre}:",
                                          QLineEdit.EchoMode.Password)
        if not ok:
            return
        nueva = self._pedir_clave_nueva()
        if nueva is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            p.renovar(actual, nueva)
        except ValueError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, nucleo.NOMBRE, str(e))
            return
        QApplication.restoreOverrideCursor()
        self.mostrar_firma()
        QMessageBox.information(self, nucleo.NOMBRE, "Listo: nueva contraseña y certificado renovado. "
                                "Los documentos firmados antes se siguen verificando.")

    def _pedir_clave_nueva(self) -> str | None:
        while True:
            nueva, ok = QInputDialog.getText(self, nucleo.NOMBRE,
                                             f"Contraseña NUEVA (mínimo {nucleo.CLAVE_MIN} caracteres; "
                                             "puede ser una frase):", QLineEdit.EchoMode.Password)
            if not ok:
                return None
            if len(nueva) < nucleo.CLAVE_MIN:
                QMessageBox.warning(self, nucleo.NOMBRE, f"Debe tener al menos {nucleo.CLAVE_MIN} caracteres.")
                continue
            otra, ok = QInputDialog.getText(self, nucleo.NOMBRE, "Repite la contraseña NUEVA:",
                                            QLineEdit.EchoMode.Password)
            if not ok:
                return None
            if nueva == otra:
                return nueva
            QMessageBox.warning(self, nucleo.NOMBRE, "Las contraseñas nuevas no coinciden.")

    def revocar(self) -> None:
        p = self.firma_seleccionada()
        if not p:
            return
        texto, ok = QInputDialog.getText(
            self, nucleo.NOMBRE,
            f"REVOCAR la firma de {p.nombre}: úsalo si te robaron o perdiste la PC, el respaldo o la "
            "contraseña.\n\n· Desde este momento su certificado actual deja de ser de confianza.\n"
            "· Lo que firmó antes CON sello de tiempo se sigue verificando bien; lo firmado sin sello "
            "saldrá como «revocada».\n· Se crea un certificado nuevo con otra contraseña.\n\n"
            "Escribe REVOCAR para continuar:")
        if not ok or texto.strip().upper() != "REVOCAR":
            return
        motivo, ok = QInputDialog.getText(self, nucleo.NOMBRE, "Motivo (p. ej. «robo de la laptop»):")
        if not ok:
            return
        actual, ok = QInputDialog.getText(self, nucleo.NOMBRE,
                                          "Contraseña ACTUAL, si la recuerdas (para conservar el dibujo).\n"
                                          "Si no, déjala vacía: después tendrás que volver a dibujar la firma.",
                                          QLineEdit.EchoMode.Password)
        if not ok:
            return
        nueva = self._pedir_clave_nueva()
        if nueva is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            conservo = p.revocar(nueva, motivo.strip() or "sin motivo", actual)
        except ValueError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, nucleo.NOMBRE, str(e))
            return
        QApplication.restoreOverrideCursor()
        self.recargar_firmas(seleccionar=p.carpeta)
        QMessageBox.information(self, nucleo.NOMBRE, "Listo: certificado anterior REVOCADO y uno nuevo creado."
                                + ("" if conservo else "\n\nAhora vuelve a dibujar la firma con «Cambiar dibujo…»."))

    def exportar_cer(self) -> None:
        p = self.firma_seleccionada()
        if not p:
            return
        sugerido = str(Path(_descargas()) / f"certificado-{nucleo._slug(p.nombre)}.cer")
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar certificado público", sugerido, "Certificado (*.cer)")
        if ruta:
            shutil.copy2(p.cer, ruta)
            QMessageBox.information(self, nucleo.NOMBRE, "Guardado. Es PÚBLICO: se le puede dar a quien quiera "
                                    "comprobar los documentos firmados.")

    def eliminar_firma(self) -> None:
        p = self.firma_seleccionada()
        if not p:
            return
        texto, ok = QInputDialog.getText(
            self, nucleo.NOMBRE, f"Para eliminar la firma de {p.nombre} escribe ELIMINAR.\n"
                                 "(Los documentos que ya firmó se siguen verificando; su llave y su dibujo se "
                                 "destruyen, así que ya nadie podrá firmar con ella.)")
        if ok and texto.strip().upper() == "ELIMINAR":
            p.eliminar()
            self.recargar_firmas()

    # ---------------- 3. verificar ----------------
    def _tab_verificar(self) -> QWidget:
        w = QWidget()
        self.lbl_verif = QLabel()
        self.lbl_verif.setWordWrap(True)
        self.lbl_verif.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        b = _boton_grande("Elegir PDF firmado…")
        b.clicked.connect(self.verificar)
        fila = QHBoxLayout()
        fila.addWidget(b)
        fila.addStretch()
        resultado, lr = _tarjeta()
        lr.addWidget(self.lbl_verif)
        lr.addStretch()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_titulo("Verificar", "Revisa quién firmó un PDF y que nadie lo haya cambiado después."))
        lay.addLayout(fila)
        lay.addWidget(resultado, 1)
        return w

    def verificar(self) -> None:
        inicio = str(self.origen.parent) if self.origen else _descargas()
        ruta, _ = QFileDialog.getOpenFileName(self, "Verificar PDF firmado", inicio, "PDF (*.pdf)")
        if ruta:
            self.mostrar_verificacion(Path(ruta))

    def mostrar_verificacion(self, pdf: Path) -> None:
        self.tabs.setCurrentIndex(2)
        try:
            ok, texto = nucleo.verificar_texto(pdf)
        except Exception as e:
            ok, texto = False, f"No se pudo revisar: {e}"
        color, encabezado = (VERDE_OK, "✔ Firmas válidas") if ok else (ROJO, "✘ Atención")
        self.lbl_verif.setText(f"<h3 style='color:{color};margin-bottom:4px'>{encabezado}</h3>"
                               f"<b>{pdf.name}</b><br><br>" + texto.replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;"))


    # ---------------- 4. archivo ----------------
    def _tab_archivo(self) -> QWidget:
        w = QWidget()
        self.txt_buscar = QLineEdit()
        self.txt_buscar.setPlaceholderText("Buscar por folio (FE-XXXX-XXXX), persona o documento…")
        self.txt_buscar.textChanged.connect(lambda _t: self.recargar_archivo())
        b_hoja = _boton_grande("Revisar hoja impresa (foto o escaneo)…")
        b_hoja.clicked.connect(self.revisar_hoja)
        fila = QHBoxLayout()
        fila.addWidget(self.txt_buscar, 1)
        fila.addWidget(b_hoja)
        self.lst_archivo = QListWidget()
        self.lst_archivo.setWordWrap(True)
        self.lst_archivo.currentRowChanged.connect(lambda _r: self.mostrar_archivado())
        self.lbl_archivado = QLabel()
        self.lbl_archivado.setWordWrap(True)
        self.lbl_archivado.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.img_archivado = QLabel()
        self.img_archivado.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_archivado.setMinimumHeight(260)
        # Que la imagen no empuje el tamaño del recuadro (si no, se recorta y se ve solo el centro)
        self.img_archivado.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        b_abrir = QPushButton("Abrir el original")
        b_abrir.clicked.connect(lambda: self._entrada_actual() and subprocess.Popen(
            ["xdg-open", str(Path(self._entrada_actual()["carpeta"]) / "firmado.pdf")], start_new_session=True))
        b_comparar = QPushButton("Comparar con otro PDF…")
        b_comparar.clicked.connect(self.comparar_con_pdf)
        b_carpeta = QPushButton("Abrir carpeta")
        b_carpeta.clicked.connect(lambda: self._entrada_actual() and subprocess.Popen(
            ["xdg-open", str(self._entrada_actual()["carpeta"])], start_new_session=True))
        self.botones_archivo = [b_abrir, b_comparar, b_carpeta]
        fb = QHBoxLayout()
        for b in self.botones_archivo:
            fb.addWidget(b)
        der, ld = _tarjeta()
        ld.addWidget(self.lbl_archivado)
        ld.addWidget(self.img_archivado, 1)
        ld.addLayout(fb)
        partir = QSplitter()
        partir.addWidget(self.lst_archivo)
        partir.addWidget(der)
        partir.setSizes([360, 640])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_titulo("Archivo", "Cada documento que firmas se guarda aquí con su folio. Con la hoja "
                                         "impresa (o una foto o escaneo de ella) encuentras el original para "
                                         "comparar: lo que vale es el PDF, no el papel."))
        lay.addLayout(fila)
        lay.addWidget(partir, 1)
        self.recargar_archivo()
        return w

    def recargar_archivo(self, seleccionar: str | None = None) -> None:
        filtro = self.txt_buscar.text().strip().lower()
        folio_buscado = nucleo.normalizar_folio(filtro) if filtro else None
        self.lst_archivo.blockSignals(True)
        self.lst_archivo.clear()
        for e in nucleo.listar_archivo():
            texto = f"{e['folio']} · {e.get('documento', '')} · {e.get('firmo', '')}".lower()
            if filtro and filtro not in texto and folio_buscado != e["folio"]:
                continue
            fecha = dt.datetime.fromisoformat(e["fecha"])
            it = QListWidgetItem(f"{e['folio']}  ·  {fecha:%d/%m/%Y %H:%M}\n{e.get('documento', '')}\n"
                                 f"Firmó: {e.get('firmo', '')}")
            it.setData(Qt.ItemDataRole.UserRole, e["folio"])
            self.lst_archivo.addItem(it)
            if seleccionar == e["folio"]:
                self.lst_archivo.setCurrentItem(it)
        self.lst_archivo.blockSignals(False)
        if self.lst_archivo.currentRow() < 0 and self.lst_archivo.count():
            self.lst_archivo.setCurrentRow(0)
        self.mostrar_archivado()

    def _entrada_actual(self) -> dict | None:
        it = self.lst_archivo.currentItem()
        return nucleo.buscar_folio(it.data(Qt.ItemDataRole.UserRole)) if it else None

    def mostrar_archivado(self, aviso: str = "") -> None:
        e = self._entrada_actual()
        for b in self.botones_archivo:
            b.setEnabled(e is not None)
        self.img_archivado.clear()
        if not e:
            self.lbl_archivado.setText(aviso or "<span style='color:gray'>Todavía no hay documentos firmados "
                                                "en el archivo (se guardan solos al firmar).</span>")
            return
        ok, texto = nucleo.revisar_archivado(e)
        color = VERDE_OK if ok else ROJO
        fecha = dt.datetime.fromisoformat(e["fecha"])
        self.lbl_archivado.setText(
            (aviso + "<br><br>" if aviso else "")
            + f"<h3 style='margin:0'>Folio {e['folio']}</h3><b>{e.get('documento', '')}</b><br>"
            f"Firmó {e.get('firmo', '')} el {fecha:%d/%m/%Y a las %H:%M}"
            + ("" if e.get("con_sello") else " (sin sello de tiempo)") + "<br>"
            f"<span style='color:{color}'>{'✔ Copia intacta y firmas válidas' if ok else '✘ Atención'}</span><br>"
            f"<small>{texto.replace(chr(10), '<br>')}</small><br>"
            "<small>Compara la hoja impresa con esta página del original, renglón por renglón:</small>")
        try:
            import pymupdf
            with pymupdf.open(Path(e["carpeta"]) / "firmado.pdf") as doc:
                pag = doc[min(int(e.get("pagina", 0)), len(doc) - 1)]
                pm = pag.get_pixmap(dpi=110)
                img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy()
            caja = self.img_archivado.contentsRect().size()
            ancho, alto = max(caja.width(), 300), max(caja.height(), 300)
            self.img_archivado.setPixmap(QPixmap.fromImage(img).scaled(
                ancho, alto, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass

    def revisar_hoja(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Foto o escaneo de la hoja firmada", _descargas(),
                                              "Fotos y escaneos (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp *.pdf)")
        if not ruta:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            textos = nucleo.leer_qr(Path(ruta))
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, nucleo.NOMBRE, f"No se pudo leer la imagen:\n{ex}")
            return
        QApplication.restoreOverrideCursor()
        folios = [(nucleo.folio_en(t), t) for t in textos if nucleo.folio_en(t)]
        if not folios:
            QMessageBox.information(self, nucleo.NOMBRE,
                                    "No encontré el código QR de la firma en esa imagen.\n\n"
                                    "Prueba con una foto más cercana y derecha, o escribe el folio que viene "
                                    "impreso junto a la firma en el buscador.")
            return
        folio, texto = folios[-1]
        e = nucleo.buscar_folio(folio)
        if not e:
            QMessageBox.warning(self, nucleo.NOMBRE,
                                f"La hoja trae el folio {folio}, pero no está en el Archivo de esta PC "
                                f"(¿se firmó en otra computadora?).\n\nLo que dice el QR:\n{texto}")
            return
        huella_qr = re.search(r"Huella:\s*([0-9A-F]+)", texto)
        coincide = huella_qr and e.get("huella_base", "").startswith(huella_qr.group(1))
        aviso = (f"<span style='color:{VERDE_OK}'>✔ La hoja corresponde al folio {folio} del Archivo.</span>"
                 if coincide else
                 f"<span style='color:{ROJO}'>✘ El QR dice folio {folio}, pero su huella NO coincide con la del "
                 "archivo: el QR pudo copiarse de otro documento.</span>")
        if len(folios) > 1:
            aviso += f"<br><small>La hoja tiene {len(folios)} firmas con QR; se muestra la última.</small>"
        self.txt_buscar.blockSignals(True)
        self.txt_buscar.clear()
        self.txt_buscar.blockSignals(False)
        self.recargar_archivo(seleccionar=folio)
        self.mostrar_archivado(aviso)

    def comparar_con_pdf(self) -> None:
        e = self._entrada_actual()
        if not e:
            return
        ruta, _ = QFileDialog.getOpenFileName(self, f"PDF para comparar con el folio {e['folio']}", _descargas(),
                                              "PDF (*.pdf)")
        if ruta:
            QMessageBox.information(self, nucleo.NOMBRE, nucleo.comparar_pdf(Path(ruta), e))


def abrir(docs: list[Path], verificar: Path | None = None) -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("firmaelectronica")
    app.setDesktopFileName("firmaelectronica")
    app.setStyle("Fusion")
    app.setPalette(estilo.paleta())
    app.setStyleSheet(estilo.QSS)
    v = Ventana(docs)
    if verificar:
        v.mostrar_verificacion(verificar)
    v.show()
    app.exec()

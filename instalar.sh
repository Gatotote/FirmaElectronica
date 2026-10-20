#!/bin/sh
# Instala Firma Electrónica para el usuario actual (sin sudo): prepara el entorno con uv y crea
# el acceso en el menú, en el Escritorio y en el clic derecho de Dolphin (KDE).
# Para quitar los accesos: ./instalar.sh quitar
set -e
AQUI="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="$HOME/.local/share/applications"
MENUS="$HOME/.local/share/kio/servicemenus"
ESCRITORIO="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [ "$1" = "quitar" ]; then
    rm -f "$APPS/firmaelectronica.desktop" "$ESCRITORIO/firmaelectronica.desktop" \
          "$MENUS/firma-electronica.desktop" "$MENUS/firma-electronica-verificar.desktop"
    echo "Accesos quitados (tus firmas en ~/.local/share/firma-electronica no se tocan)."
    exit 0
fi

command -v uv >/dev/null || { echo "Falta uv: https://docs.astral.sh/uv/"; exit 1; }
(cd "$AQUI" && uv sync --quiet)
chmod +x "$AQUI/ejecutar.sh"

MIMES="application/pdf;application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/msword;application/vnd.oasis.opendocument.text;application/rtf;text/rtf;application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;application/vnd.ms-excel;application/vnd.oasis.opendocument.spreadsheet;application/vnd.openxmlformats-officedocument.presentationml.presentation;application/vnd.ms-powerpoint;application/vnd.oasis.opendocument.presentation;image/jpeg;image/png;image/webp;image/tiff;image/bmp;text/plain;"
ICONO="$AQUI/firmaelectronica/icono.png"

mkdir -p "$APPS" "$MENUS"
cat > "$APPS/firmaelectronica.desktop" <<EOD
[Desktop Entry]
Type=Application
Name=Firma Electrónica
Comment=Firma documentos (PDF, Word, Excel, imágenes) con firma dibujada + firma electrónica
Exec="$AQUI/ejecutar.sh" ventana %F
Icon=$ICONO
MimeType=$MIMES
Terminal=false
Categories=Office;Utility;
StartupWMClass=firmaelectronica
EOD
if [ -d "$ESCRITORIO" ]; then
    cp "$APPS/firmaelectronica.desktop" "$ESCRITORIO/"
    chmod +x "$ESCRITORIO/firmaelectronica.desktop"
fi
cat > "$MENUS/firma-electronica.desktop" <<EOD
[Desktop Entry]
Type=Service
MimeType=$MIMES
Actions=firmar;

[Desktop Action firmar]
Name=Firmar con Firma Electrónica
Icon=$ICONO
Exec="$AQUI/ejecutar.sh" ventana %F
EOD
cat > "$MENUS/firma-electronica-verificar.desktop" <<EOD
[Desktop Entry]
Type=Service
MimeType=application/pdf;
Actions=verificar;

[Desktop Action verificar]
Name=Verificar firma electrónica
Icon=document-preview
Exec="$AQUI/ejecutar.sh" ventana-verificar %f
EOD
chmod +x "$MENUS"/firma-electronica*.desktop
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
echo "Listo: busca «Firma Electrónica» en el menú o en el Escritorio."

#!/bin/bash
# Programa las tres tareas en este Mac con launchd (hora local de Madrid).
#   tareas/programar.sh activar    # 7:00 todos los días; 15:45 y 22:10 de lunes a viernes
#   tareas/programar.sh quitar
#   tareas/programar.sh estado
#
# launchd no despierta un Mac dormido: si a la hora está en reposo, la tarea
# se ejecuta al despertar. La primera vez, macOS puede pedir permiso para que
# el programa acceda a la carpeta del Escritorio: hay que aceptarlo.
set -e
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
DESTINO="$HOME/Library/LaunchAgents"
USUARIO="gui/$(id -u)"

laborables() {   # $1 hora, $2 minuto
  echo "<array>"
  for d in 1 2 3 4 5; do
    echo "<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>$1</integer><key>Minute</key><integer>$2</integer></dict>"
  done
  echo "</array>"
}

escribir() {     # $1 tarea, $2 bloque de horario
  cat > "$DESTINO/com.botbolsa.$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.botbolsa.$1</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$RAIZ/tareas/ejecutar.sh</string><string>$1</string></array>
  <key>StartCalendarInterval</key>$2
  <key>StandardErrorPath</key><string>$RAIZ/registros/launchd-$1.log</string>
  <key>StandardOutPath</key><string>$RAIZ/registros/launchd-$1.log</string>
</dict></plist>
EOF
}

case "$1" in
  activar)
    mkdir -p "$DESTINO" "$RAIZ/registros"
    escribir manana "<dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>"
    escribir apertura "$(laborables 15 45)"
    escribir cierre "$(laborables 22 10)"
    for t in manana apertura cierre; do
      launchctl bootout "$USUARIO/com.botbolsa.$t" 2>/dev/null || true
      launchctl bootstrap "$USUARIO" "$DESTINO/com.botbolsa.$t.plist"
    done
    echo "Tareas activadas: 7:00 (diaria), 15:45 y 22:10 (lunes a viernes)."
    ;;
  quitar)
    for t in manana apertura cierre; do
      launchctl bootout "$USUARIO/com.botbolsa.$t" 2>/dev/null || true
      rm -f "$DESTINO/com.botbolsa.$t.plist"
    done
    echo "Tareas quitadas."
    ;;
  estado)
    launchctl list | grep botbolsa || echo "No hay tareas activas."
    ;;
  probar)
    # Lanza una comprobación inofensiva desde launchd, igual que se lanzarán las tareas:
    # confirma que el Mac deja leer la carpeta, encuentra los programas y llama a un modelo.
    mkdir -p "$DESTINO" "$RAIZ/registros"
    REG="$RAIZ/registros/$(date +%Y-%m-%d)-comprobar.log"
    rm -f "$REG"
    cat > "$DESTINO/com.botbolsa.comprobar.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.botbolsa.comprobar</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$RAIZ/tareas/ejecutar.sh</string><string>comprobar</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
    launchctl bootout "$USUARIO/com.botbolsa.comprobar" 2>/dev/null || true
    launchctl bootstrap "$USUARIO" "$DESTINO/com.botbolsa.comprobar.plist"
    for i in $(seq 1 30); do
      grep -q "comprobación" "$REG" 2>/dev/null && break
      sleep 3
    done
    launchctl bootout "$USUARIO/com.botbolsa.comprobar" 2>/dev/null || true
    rm -f "$DESTINO/com.botbolsa.comprobar.plist"
    if [ -s "$REG" ]; then cat "$REG"; else echo "Sin respuesta en 90 s: el Mac no ha dejado ejecutar la tarea (revisa el permiso de acceso al Escritorio)."; exit 1; fi
    ;;
  *)
    echo "Uso: tareas/programar.sh activar | quitar | estado | probar"; exit 1
    ;;
esac

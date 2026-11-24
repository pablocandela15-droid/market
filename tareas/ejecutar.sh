#!/bin/bash
# Lanza una tarea del bot y guarda su registro.
#   tareas/ejecutar.sh manana | apertura | cierre | mirofish
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$HOME/.docker/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "$0")/.." || exit 1
mkdir -p registros
python3 -m bot.tarea "$@" >> "registros/$(date +%Y-%m-%d)-$1.log" 2>&1

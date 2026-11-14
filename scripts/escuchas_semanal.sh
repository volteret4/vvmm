#!/bin/bash
# Cron semanal en pepecono (lunes): genera la semana de escuchas recién cerrada y la publica.
# Lee los scrobbles en local (--host local), hace commit solo de content/escuchas y
# assets/escuchas-portadas, y hace push con la clave de GitHub de pepecono; el push
# lanza el workflow de GitHub Pages.
set -euo pipefail
cd "$(dirname "$0")/.."
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github -o IdentitiesOnly=yes -o BatchMode=yes"

python3 -I scripts/escuchas.py --ultima --host local

git add -A content/escuchas assets/escuchas-portadas
if git diff --cached --quiet; then
    echo "escuchas: sin cambios"
    exit 0
fi
semana=$(date -d '7 days ago' +%G-W%V)
git commit -q -m "Escuchas $semana" -- content/escuchas assets/escuchas-portadas
git pull -q --rebase --autostash origin main
git push -q origin HEAD:main
echo "escuchas: publicada $semana"

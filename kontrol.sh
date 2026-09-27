#!/usr/bin/env bash
# Dağıtım öncesi hızlı denetim.
#
# pyflakes burada ASIL İŞE YARIYOR: tanımsız isimleri yakalar. Bir düzenleme
# sırasında last_seen/remember_seen tanımları yanlışlıkla silinmişti; modül
# yine de import oluyordu ve hata ancak temizlik BAŞLADIĞINDA patlıyordu.
# Bu denetim onu ilk saniyede yakalar.
set -uo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
hata=0

echo "── tanımsız isim denetimi ────────────────────"
if ! $PY -m pyflakes app/*.py ./*.py 2>&1 | grep -i "undefined name"; then
    echo "  ✅ tanımsız isim yok"
else
    echo "  ❌ TANIMSIZ İSİM VAR"; hata=1
fi

echo
echo "── kritik isimler yerinde mi ─────────────────"
$PY - <<'PYEOF' || hata=1
from app import temizlik as t, yedek
eksik = [a for a in (
    "last_seen", "remember_seen", "_load_seen", "find_latest_id",
    "clean_after", "sweep", "user_sweep", "prepare_job", "yedek_kapat",
) if not hasattr(t, a)]
eksik += ["yedek." + a for a in ("yetki_ver", "yetki_al", "sohbet_tipi") if not hasattr(yedek, a)]
print("  ❌ EKSİK: " + ", ".join(eksik)) if eksik else print("  ✅ hepsi yerinde")
raise SystemExit(1 if eksik else 0)
PYEOF

echo
echo "── bot.py tek başına açılıyor mu ─────────────"
BOT="../tg mesaj silme/bot.py"
if [[ -f $BOT ]]; then
    $PY -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('_kontrol', '$BOT')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print('  ✅ bot.py tek başına yüklendi')
" || hata=1
else
    echo "  (bot.py bulunamadı, atlandı)"
fi

echo
[[ $hata -eq 0 ]] && echo "✅ DENETİM TEMİZ" || echo "❌ DENETİM BAŞARISIZ"
exit $hata

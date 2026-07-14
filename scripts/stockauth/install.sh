#!/bin/bash
# stockauth 一键安装（在 ECS root 下执行）。幂等：重复执行安全。
# curl -s "https://api.github.com/repos/chenyanchong321/us-stock-notes/contents/scripts/stockauth/install.sh" -H "Accept: application/vnd.github.raw" | bash
set -e
D=/root/stockauth
RAW="https://api.github.com/repos/chenyanchong321/us-stock-notes/contents/scripts/stockauth"
H='Accept: application/vnd.github.raw'

mkdir -p $D
echo "== 拉取代码 =="
curl -s "$RAW/server.py" -H "$H" -o $D/server.py
curl -s "$RAW/manage.py" -H "$H" -o $D/manage.py
python3 -m py_compile $D/server.py $D/manage.py && echo "语法 OK"

echo "== 初始点位导入（仅首次；来源=本机已同步的网站配置） =="
if [ ! -f $D/points.json ] && [ -f /var/www/us-stock/config/buypoints.json ]; then
  cd $D && python3 manage.py import /var/www/us-stock/config/buypoints.json
fi

echo "== systemd 服务 =="
cat > /etc/systemd/system/stockauth.service <<'EOF'
[Unit]
Description=stockauth points membership service
After=network.target

[Service]
ExecStart=/usr/bin/python3 /root/stockauth/server.py
WorkingDirectory=/root/stockauth
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now stockauth
sleep 1
systemctl is-active stockauth

echo "== nginx /api/ 反代（幂等） =="
NG=/etc/nginx/sites-enabled/us-stock
if ! grep -q stockauth $NG; then
  # 插到 443 server 块的 "location / {" 之前
  sed -i '0,/    location \/ {/s//    location \/api\/ { # stockauth\n        proxy_pass http:\/\/127.0.0.1:8600;\n        proxy_set_header X-Real-IP $remote_addr;\n    }\n\n    location \/ {/' $NG
  nginx -t && systemctl reload nginx
  echo "nginx 已加 /api/ 反代"
else
  echo "nginx 已有 /api/ 反代，跳过"
fi

echo "== 每周日自动备份账号库与点位 =="
( crontab -l 2>/dev/null | grep -v stockauth-backup ; echo '0 6 * * 0 cd /root/stockauth && /usr/bin/python3 manage.py export >> /root/stockauth/backup.log 2>&1 # stockauth-backup' ) | crontab -

echo "== 自检 =="
curl -s http://127.0.0.1:8600/api/health && echo
curl -s https://stock.ziyuanai.top/api/health && echo
echo "安装完成"

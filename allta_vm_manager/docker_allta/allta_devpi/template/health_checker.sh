#! /bin/bash
set -e

code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3141/+api)
if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
exit 0
else
exit 1
fi
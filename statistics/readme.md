# Программный модуль для статистических подсчетов результатов нагрузочных испытаний в confluence

## WEB API

```
sudo apt install docker.io
sudo apt install docker-compose

sudo docker-compose build
sudo docker-compose up -d
```

## CLI
### Предварительные действия
```
python3.12 -m venv venv
source venv/bin/activate
pip3 install -r req.txt
```

### Запуск cli
```
python3 main.py -u "username" -t "token"
```
### Запуск

```bash
sudo perf record -g -a ./load2noarch /home/u/test/ 120 8000 5   
sudo perf script | perl libstackcollapse-perf.pl | perl libflamegraph.pl > 120_8000_5.svg   
```

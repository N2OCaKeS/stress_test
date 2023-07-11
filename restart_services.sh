#!/bin/bash

sudo systemctl daemon-reload
sudo systemctl restart bendiks.service
sudo systemctl restart nginx.service
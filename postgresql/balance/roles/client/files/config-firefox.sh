#!/bin/bash
tput setaf 2

ff=$(find ~/.mozilla/firefox/ -type d -name '*.default-release')
if [ -z "$ff" ] || [ ! -e "$ff"/user.js ]
then
  echo "Running firefox configuration..."
  timeout 3 firefox --first-startup --headless &> /dev/null
  ff=$(find ~/.mozilla/firefox/ -type d -name '*.default-release')
  echo $'user_pref("network.negotiate-auth.delegation-uris", "http://,https://");\nuser_pref("network.negotiate-auth.trusted-uris", "http://,https://");' > "$ff"/user.js
  echo "Done!"
fi

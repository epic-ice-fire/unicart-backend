#!/usr/bin/env bash

# Install Flutter in writable directory
if [ ! -d "$HOME/flutter" ]; then
  git clone https://github.com/flutter/flutter.git -b stable $HOME/flutter
fi

export PATH="$PATH:$HOME/flutter/bin"

flutter doctor
flutter pub get
flutter build web --release
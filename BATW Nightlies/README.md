# ffmpeg build configuration

```
./configure \
--prefix=/opt/ffmpeg \
--enable-gpl \
--enable-libx264 \
--enable-libfreetype \
--enable-libfontconfig \
--enable-libharfbuzz \
--enable-libfribidi \
--extra-cflags="-I/opt/homebrew/include" \
--extra-ldflags="-L/opt/homebrew/lib" \
--pkg-config-flags="--static"
```

```
make -j"$(sysctl -n hw.ncpu)"
sudo make install
```

Make sure to add to path
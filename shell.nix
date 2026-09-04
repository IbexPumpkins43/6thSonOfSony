# shell.nix
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [
    deno
    pkg-config

    cairo
    pango
    libjpeg
    giflib
    librsvg
    pixman
  ];

  LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
    pkgs.cairo
    pkgs.pango
    pkgs.libjpeg
    pkgs.giflib
    pkgs.librsvg
    pkgs.pixman
  ];
}

{
  description = "Dukebox - a Discord music bot written in Rust";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;

      pkgsFor = system: import nixpkgs {
        inherit system;
      };
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = with pkgs; [
              cargo
              rustc
              rustfmt
              clippy

              pkg-config
              opus
              ffmpeg
              yt-dlp
            ];

            shellHook = ''
              echo "Dukebox development environment"
              echo "  cargo run --release"
              echo "  cargo clippy"
              echo "  cargo fmt"
            '';
          };
        });

      apps = forAllSystems (system:
        let
          pkgs = pkgsFor system;

          dukebox-run = pkgs.writeShellApplication {
            name = "dukebox";

            runtimeInputs = with pkgs; [
              cargo
              rustc
              pkg-config
              opus
              ffmpeg
              yt-dlp
            ];

            text = ''
              if [ ! -f Cargo.toml ]; then
                echo "Run 'nix run' from the Dukebox project directory." >&2
                exit 1
              fi

              exec cargo run --release -- "$@"
            '';
          };
        in
        {
          default = {
            type = "app";
            program = "${dukebox-run}/bin/dukebox";
          };
        });
    };
}

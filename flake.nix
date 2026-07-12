{
  description = "Photography timelapse compositing tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      # Support both x86_64 and aarch64 darwin systems
      supportedSystems = [ "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            buildInputs = with pkgs; [
              imagemagick
              bash
              coreutils
            ];
            shellHook = ''
              echo "Photography timelapse tools environment ready"
              echo "ImageMagick: $(convert -version | head -1)"
            '';
          };
        }
      );
    };
}


{
  description = "Photography timelapse compositing + subject auto-crop tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = { self, nixpkgs }:
    let
      # Support both x86_64 and aarch64 darwin systems
      supportedSystems = [ "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;

      # nixpkgs with an overlay that skips seaborn's flaky test (it fails on
      # aarch64-darwin and would otherwise break the ultralytics/torch closure).
      pkgsFor = system: import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        overlays = [
          (final: prev: {
            pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
              (pfinal: pprev: {
                seaborn = pprev.seaborn.overridePythonAttrs (_: {
                  doCheck = false;
                  doInstallCheck = false;
                });
              })
            ];
          })
        ];
      };
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          acPkgs = pkgsFor system;
          acPython = acPkgs.python3.withPackages (ps: with ps; [
            ultralytics   # YOLO person detection
            rawpy         # RAW decode (libraw)
            pillow
            numpy
            opencv4
          ]);
        in
        {
          # Existing lightweight timelapse/stacking environment (unchanged).
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

          # Subject auto-crop + parallel export tools (autocrop/).
          # Enter with:  nix develop .#autocrop
          autocrop = acPkgs.mkShell {
            # ffmpeg: encodes the subject-tracked section clips (section_movie.py)
            buildInputs = [ acPython acPkgs.exiftool acPkgs.coreutils
                            acPkgs.ffmpeg ];
            shellHook = ''
              # keep model weights + caches out of the way and writable
              export YOLO_CONFIG_DIR="''${YOLO_CONFIG_DIR:-$PWD/.cache/ultralytics}"
              export MPLCONFIGDIR="''${MPLCONFIGDIR:-$PWD/.cache/mpl}"
              mkdir -p "$YOLO_CONFIG_DIR" "$MPLCONFIGDIR"
              echo "autocrop environment ready: $(python --version 2>&1), exiftool $(exiftool -ver)"
              echo "darktable-cli (for export/proofs) expected on PATH or at"
              echo "  /Applications/darktable.app/Contents/MacOS/darktable-cli (override: DARKTABLE_CLI)"
            '';
          };
        }
      );
    };
}

{
  description = "Canvas API, interactive CLI, and local course context mirror";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      supportedSystems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: import nixpkgs { inherit system; };
      project = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python3;
        in
        rec {
          canvas-tools = python.pkgs.buildPythonApplication {
            pname = project.name;
            inherit (project) version;
            pyproject = true;
            src = ./.;

            build-system = [ python.pkgs.setuptools ];
            dependencies =
              with python.pkgs;
              [
                requests
                tabulate
              ]
              ++ pkgs.lib.optionals (python.pythonOlder "3.11") [ tomli ];

            nativeCheckInputs = [ python.pkgs.pytestCheckHook ];
            pythonImportsCheck = [
              "canvasapi"
              "canvascli"
              "canvasmirror"
            ];

            meta = {
              inherit (project) description;
              platforms = supportedSystems;
            };
          };

          default = canvas-tools;
        }
      );

      apps = forAllSystems (
        system:
        let
          package = self.packages.${system}.default;
        in
        {
          canvascli = {
            type = "app";
            program = "${package}/bin/canvascli";
            meta.description = "Interactively inspect and download Canvas course resources";
          };
          canvasmirror = {
            type = "app";
            program = "${package}/bin/canvasmirror";
            meta.description = "Maintain local, agent-readable Canvas course mirrors";
          };
          default = self.apps.${system}.canvascli;
        }
      );

      checks = forAllSystems (system: {
        package = self.packages.${system}.default;
      });

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python3;
          pythonEnvironment = python.withPackages (
            ps:
            [
              ps.pytest
              ps.requests
              ps.setuptools
              ps.tabulate
            ]
            ++ pkgs.lib.optionals (python.pythonOlder "3.11") [ ps.tomli ]
          );
        in
        {
          default = pkgs.mkShell {
            packages = [ pythonEnvironment ];
          };
        }
      );
    };
}

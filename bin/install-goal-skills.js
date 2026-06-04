#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");

const packageRoot = path.resolve(__dirname, "..");
const packageJson = require(path.join(packageRoot, "package.json"));
const skillsRoot = path.join(packageRoot, "skills");
const supportDirNames = ["_goal_shared"];
const metadataFiles = ["AGENTS.md", "README.md", "maintenance/agent-context-index.json"];

function usage() {
  return `Install Codex goal orchestration skills.

Usage:
  npx github:jc1122/codex-goal-orchestration-skills [options]

Options:
  --dest <dir>   Install destination. Defaults to $CODEX_HOME/skills or ~/.codex/skills.
  --list         List bundled skills without installing.
  --version      Print package version.
  --dry-run      Print planned installs without copying.
  --force        Accepted for explicit overwrite intent; matching skill dirs are replaced.
  -h, --help     Show help.
`;
}

function parseArgs(argv) {
  const args = { dest: null, list: false, version: false, dryRun: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--") {
      continue;
    } else if (arg === "--dest") {
      if (i + 1 >= argv.length) {
        throw new Error("--dest requires a directory");
      }
      args.dest = argv[i + 1];
      i += 1;
    } else if (arg === "--list") {
      args.list = true;
    } else if (arg === "--version") {
      args.version = true;
    } else if (arg === "--dry-run") {
      args.dryRun = true;
    } else if (arg === "--force") {
      // Overwrite is the default for fs.cpSync({ force: true }); the flag is accepted for scripts.
    } else if (arg === "-h" || arg === "--help") {
      args.help = true;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return args;
}

function defaultDest() {
  if (process.env.CODEX_HOME) {
    return path.join(process.env.CODEX_HOME, "skills");
  }
  return path.join(os.homedir(), ".codex", "skills");
}

function hasTraversal(value) {
  return value.split(/[\\/]+/).includes("..");
}

function bundledSkills() {
  return fs
    .readdirSync(skillsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .filter((entry) => fs.existsSync(path.join(skillsRoot, entry.name, "SKILL.md")))
    .map((entry) => entry.name)
    .sort();
}

function bundledSupportDirs() {
  return supportDirNames.filter((name) => {
    const supportDir = path.join(skillsRoot, name);
    return fs.existsSync(supportDir) && fs.statSync(supportDir).isDirectory();
  });
}

function isSameOrInside(candidate, parent) {
  const relative = path.relative(parent, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function validateDestRoot(destRoot) {
  if (!path.isAbsolute(destRoot)) {
    throw new Error("--dest must be an absolute path");
  }
  if (hasTraversal(destRoot)) {
    throw new Error("--dest must not contain '..' traversal");
  }
  const resolved = path.resolve(destRoot);
  if (resolved === path.parse(resolved).root) {
    throw new Error("refusing to install skills directly into a filesystem root");
  }

  const realPackageRoot = fs.realpathSync(packageRoot);
  if (isSameOrInside(resolved, realPackageRoot) || isSameOrInside(realPackageRoot, resolved)) {
    throw new Error("refusing to install into the package source tree");
  }
  return resolved;
}

function copyBundledDir(name, destRoot, dryRun, label) {
  const src = path.join(skillsRoot, name);
  const dest = path.join(destRoot, name);
  if (dryRun) {
    console.log(`[dry-run] ${src} -> ${dest}`);
    return;
  }
  fs.mkdirSync(destRoot, { recursive: true });
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(src, dest, { recursive: true, force: true });
  console.log(`installed ${label} ${name} -> ${dest}`);
}

function copySkill(name, destRoot, dryRun) {
  copyBundledDir(name, destRoot, dryRun, "skill");
}

function copySupportDir(name, destRoot, dryRun) {
  copyBundledDir(name, destRoot, dryRun, "support");
}

function copyMetadataFile(relativePath, destRoot, dryRun) {
  const src = path.join(packageRoot, relativePath);
  const dest = path.join(destRoot, relativePath);
  if (dryRun) {
    console.log(`[dry-run] ${src} -> ${dest}`);
    return;
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.rmSync(dest, { force: true });
  fs.copyFileSync(src, dest);
  console.log(`installed metadata ${relativePath} -> ${dest}`);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return;
  }
  if (args.version) {
    console.log(packageJson.version);
    return;
  }

  const skills = bundledSkills();
  if (args.list) {
    for (const skill of skills) {
      console.log(skill);
    }
    return;
  }

  const destRoot = validateDestRoot(args.dest || defaultDest());
  const supportDirs = bundledSupportDirs();
  for (const skill of skills) {
    copySkill(skill, destRoot, args.dryRun);
  }
  for (const supportDir of supportDirs) {
    copySupportDir(supportDir, destRoot, args.dryRun);
  }
  for (const metadataFile of metadataFiles) {
    copyMetadataFile(metadataFile, destRoot, args.dryRun);
  }

  if (!args.dryRun) {
    const supportLabel = supportDirs.length === 1 ? "support directory" : "support directories";
    console.log(
      `Installed ${skills.length} skills, ${supportDirs.length} ${supportLabel}, and ${metadataFiles.length} metadata files. Restart Codex or start a new session to refresh skill discovery.`
    );
  }
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}

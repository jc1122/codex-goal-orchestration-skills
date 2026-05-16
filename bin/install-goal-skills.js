#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");

const packageRoot = path.resolve(__dirname, "..");
const skillsRoot = path.join(packageRoot, "skills");

function usage() {
  return `Install Codex goal orchestration skills.

Usage:
  npx github:jc1122/codex-goal-orchestration-skills [options]

Options:
  --dest <dir>   Install destination. Defaults to $CODEX_HOME/skills or ~/.codex/skills.
  --list         List bundled skills without installing.
  --dry-run      Print planned installs without copying.
  --force        Accepted for explicit overwrite intent; matching skill dirs are replaced.
  -h, --help     Show help.
`;
}

function parseArgs(argv) {
  const args = { dest: null, list: false, dryRun: false, help: false };
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
    .map((entry) => entry.name)
    .sort();
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

function copySkill(name, destRoot, dryRun) {
  const src = path.join(skillsRoot, name);
  const dest = path.join(destRoot, name);
  if (dryRun) {
    console.log(`[dry-run] ${src} -> ${dest}`);
    return;
  }
  fs.mkdirSync(destRoot, { recursive: true });
  fs.rmSync(dest, { recursive: true, force: true });
  fs.cpSync(src, dest, { recursive: true, force: true });
  console.log(`installed ${name} -> ${dest}`);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
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
  for (const skill of skills) {
    copySkill(skill, destRoot, args.dryRun);
  }

  if (!args.dryRun) {
    console.log(`Installed ${skills.length} skills. Restart Codex or start a new session to refresh skill discovery.`);
  }
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}

#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createInterface } from 'node:readline/promises'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const branches = {
  backend: 'feature/backend-evaluation',
  frontend: 'feature/frontend-map',
}

function git(args, { inherit = false, allowFailure = false } = {}) {
  const result = spawnSync('git', args, {
    cwd: repoRoot,
    encoding: 'utf8',
    stdio: inherit ? 'inherit' : 'pipe',
    windowsHide: true,
  })

  if (result.error) throw result.error
  if (result.status !== 0 && !allowFailure) {
    const detail = result.stderr?.trim() || `kod zakończenia: ${result.status}`
    throw new Error(`Git nie wykonał polecenia: ${detail}`)
  }
  return result
}

function usage() {
  console.log(`Użycie:
  node scripts/team-git.mjs sync backend|frontend
  node scripts/team-git.mjs publish backend|frontend [-m "Opis commita"]
  node scripts/team-git.mjs publish backend --include-api-doc [-m "Opis commita"]

sync: pobiera origin/main i scala go z bieżącą, czystą gałęzią. Nie wysyła zmian.
publish: dodaje pliki tylko z katalogu swojej roli, tworzy commit i wysyła gałąź.
--include-api-doc: pozwala backendowi dodać docs/api.md po uzgodnieniu kontraktu.`)
}

function parseArgs(args) {
  const [action, area, ...options] = args
  if (!['sync', 'publish'].includes(action) || !branches[area]) {
    usage()
    process.exit(2)
  }

  let message = null
  let includeApiDoc = false
  for (let i = 0; i < options.length; i += 1) {
    const option = options[i]
    if (action === 'publish' && (option === '-m' || option === '--message')) {
      message = options[++i]
      if (!message?.trim()) throw new Error('Podaj niepustą treść po -m.')
    } else if (action === 'publish' && area === 'backend' && option === '--include-api-doc') {
      includeApiDoc = true
    } else {
      throw new Error(`Nieznany argument: ${option}`)
    }
  }

  return { action, area, message, includeApiDoc }
}

function assertBranch(area) {
  const actual = git(['branch', '--show-current']).stdout.trim()
  if (actual !== branches[area]) {
    throw new Error(`Jesteś na gałęzi "${actual || '(brak)'}". Dla ${area} wymagana jest "${branches[area]}".`)
  }
}

function assertNoOperationInProgress() {
  for (const ref of ['MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD']) {
    if (git(['rev-parse', '-q', '--verify', ref], { allowFailure: true }).status === 0) {
      throw new Error(`Trwa operacja Git (${ref}). Zakończ ją ręcznie przed użyciem skryptu.`)
    }
  }
  for (const name of ['rebase-merge', 'rebase-apply']) {
    const location = git(['rev-parse', '--git-path', name]).stdout.trim()
    if (existsSync(path.resolve(repoRoot, location))) {
      throw new Error('Trwa rebase. Zakończ go ręcznie przed użyciem skryptu.')
    }
  }
}

function changedPaths() {
  const records = git(['status', '--porcelain=v1', '-z', '--untracked-files=all']).stdout.split('\0')
  const paths = []
  for (let i = 0; i < records.length; i += 1) {
    const record = records[i]
    if (!record) continue
    const status = record.slice(0, 2)
    paths.push(record.slice(3))
    if (status.includes('R') || status.includes('C')) paths.push(records[++i])
  }
  return paths
}

function isAllowed(file, area, includeApiDoc) {
  return file.startsWith(`${area}/`) || (area === 'backend' && includeApiDoc && file === 'docs/api.md')
}

async function publish(area, suppliedMessage, includeApiDoc) {
  const changed = changedPaths()
  const forbidden = changed.filter((file) => !isAllowed(file, area, includeApiDoc))
  if (forbidden.length) {
    throw new Error(`Zmiany poza zakresem ${area}:\n${forbidden.join('\n')}\nNie dodano ani nie wysłano plików.`)
  }

  let message = suppliedMessage
  if (changed.length && !message) {
    if (!process.stdin.isTTY) throw new Error('Podaj wiadomość commita przez -m.')
    const prompt = createInterface({ input: process.stdin, output: process.stdout })
    try {
      message = (await prompt.question('Wiadomość commita: ')).trim()
    } finally {
      prompt.close()
    }
    if (!message) throw new Error('Wiadomość commita nie może być pusta.')
  }

  if (changed.length) {
    git(['add', '--', `${area}/`, ...(includeApiDoc ? ['docs/api.md'] : [])], { inherit: true })
    const staged = git(['diff', '--cached', '--name-only', '-z']).stdout.split('\0').filter(Boolean)
    const unexpected = staged.filter((file) => !isAllowed(file, area, includeApiDoc))
    if (unexpected.length) throw new Error(`Wstrzymano commit: pliki poza zakresem:\n${unexpected.join('\n')}`)
    if (staged.length) {
      console.log(`Pliki w commicie:\n${staged.join('\n')}`)
      git(['commit', '-m', message], { inherit: true })
    }
  }

  git(['push', '-u', 'origin', branches[area]], { inherit: true })
  console.log(`Gałąź ${branches[area]} jest wysłana do origin.`)
}

function sync(area) {
  if (changedPaths().length) {
    throw new Error('Przed sync zapisz i wyślij zmiany albo odłóż je ręcznie. Nie zmieniono gałęzi.')
  }

  git(['fetch', 'origin'], { inherit: true })
  const remoteBranch = `refs/remotes/origin/${branches[area]}`
  if (git(['show-ref', '--verify', '--quiet', remoteBranch], { allowFailure: true }).status === 0) {
    git(['merge', '--ff-only', `origin/${branches[area]}`], { inherit: true })
  }
  try {
    git(['merge', '--no-edit', 'origin/main'], { inherit: true })
  } catch (error) {
    throw new Error(`${error.message}\nJeśli wystąpił konflikt, sprawdź git status i rozwiąż go ręcznie. Niczego nie wysłano.`)
  }
  console.log('Gałąź lokalna zawiera najnowszy origin/main. Aby ją wysłać, uruchom publish.')
}

try {
  if (process.argv.includes('--help') || process.argv.includes('-h')) {
    usage()
  } else {
    const { action, area, message, includeApiDoc } = parseArgs(process.argv.slice(2))
    assertBranch(area)
    assertNoOperationInProgress()
    if (action === 'sync') sync(area)
    else await publish(area, message, includeApiDoc)
  }
} catch (error) {
  console.error(`Błąd: ${error.message}`)
  process.exitCode = 1
}

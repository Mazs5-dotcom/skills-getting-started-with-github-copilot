/**
 * Spec for exercise issue binding.
 *
 * Step workflows must comment on and close the issue created by the start
 * workflow. A newer issue whose title contains "Exercise", opened by someone
 * other than that workflow, must not receive those comments or be closed.
 *
 * The harness executes the github-script bodies the workflows actually run.
 * When a job calls skills/exercise-toolkit@v0.8.1 (commit 1b3556ba573dca34f6b2cc37532c645c6fe9f5d3),
 * the script text is the copy in tests/fixtures/exercise-toolkit/v0.8.1/, taken
 * from that tag. actions/github-script@v8 (commit ed597411d8f924073f98dfc5c65a23a2325f34cd)
 * runs that text as an async function, which is what src/async-function.ts does.
 *
 * github.rest.issues.listForRepo is GET /repos/{owner}/{repo}/issues
 * (@octokit/plugin-rest-endpoint-methods 10.0.1, src/generated/endpoints.ts).
 * The stand-in applies the query parameters from GitHub's REST description
 * (issues/list-for-repo): state, sort, direction, per_page, and page. A read of
 * the public issues API showed sort=created&direction=desc returns the newest
 * created_at first, and pull requests are included with a pull_request key.
 * @octokit/request 8.1.1 fetch-wrapper.ts returns { status, url, headers, data }.
 *
 * GrantBirki/comment@v2.1.1 (commit 608e41b19bc973020ec0e189ebfdae935d7fe0cc)
 * src/main.js posts or edits using the issue-number / comment-id inputs.
 * peter-evans/find-comment@v3 src/find.ts lists comments for that issue number.
 * gh 2.105.0 parses the issue URL in pkg/cmd/issue/shared/lookup.go
 * (ParseIssueFromArg) and closes that issue in pkg/cmd/issue/close/close.go.
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const workflowsDir = join(repoRoot, '.github', 'workflows');
const fixtureDir = join(
  repoRoot,
  'tests',
  'fixtures',
  'exercise-toolkit',
  'v0.8.1'
);
const OWNER = 'student';
const REPO = 'exercise-copy';
const TOKEN_PLACEHOLDER = 'TEST_GITHUB_TOKEN';
const ACTIONS_BOT = 'github-actions[bot]';

const STEP_WORKFLOWS = [
  '1-step.yml',
  '2-step.yml',
  '3-step.yml',
  '4-step.yml',
  '5-step.yml',
];

function loadYaml(file) {
  const result = spawnSync(
    'python3',
    [
      '-c',
      `
import json, os, yaml
data = yaml.safe_load(open(os.environ["YAML_FILE"]))
def fix(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key is True:
                key = "on"
            elif key is False:
                key = "off"
            out[str(key)] = fix(item)
        return out
    if isinstance(value, list):
        return [fix(item) for item in value]
    return value
print(json.dumps(fix(data)))
`,
    ],
    {
      encoding: 'utf8',
      env: { ...process.env, YAML_FILE: file },
    }
  );
  if (result.status !== 0) {
    throw new Error(`YAML parse failed for ${file}: ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

function truthy(value) {
  return !(
    value === false ||
    value === null ||
    value === undefined ||
    value === '' ||
    value === 0
  );
}

function evalExpr(expr, ctx) {
  let text = String(expr).trim();
  if (text.startsWith('!')) {
    return !truthy(evalExpr(text.slice(1), ctx));
  }
  const orParts = splitTop(text, '||');
  if (orParts.length > 1) {
    for (const part of orParts) {
      const value = evalExpr(part, ctx);
      if (truthy(value)) return value;
    }
    return orParts.length ? evalExpr(orParts[orParts.length - 1], ctx) : '';
  }
  const compare = text.match(/^([\s\S]+?)\s*(==|!=)\s*([\s\S]+)$/);
  if (compare && !compare[1].includes('(')) {
    const left = evalExpr(compare[1], ctx);
    const right = evalExpr(compare[3], ctx);
    return compare[2] === '==' ? left === right : left !== right;
  }
  if (
    (text.startsWith("'") && text.endsWith("'")) ||
    (text.startsWith('"') && text.endsWith('"'))
  ) {
    return text.slice(1, -1);
  }
  if (!/^[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*)*$/.test(text)) {
    return text;
  }
  let current = ctx;
  for (const part of text.split('.')) {
    if (current == null || typeof current !== 'object') return '';
    current = current[part];
  }
  if (current == null) return '';
  return current;
}

function splitTop(text, token) {
  const parts = [];
  let current = '';
  let quote = null;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quote) {
      current += char;
      if (char === quote) quote = null;
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      current += char;
      continue;
    }
    if (text.startsWith(token, i)) {
      parts.push(current);
      current = '';
      i += token.length - 1;
      continue;
    }
    current += char;
  }
  parts.push(current);
  return parts;
}

function stringify(value) {
  if (value == null) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

function interpolate(value, ctx) {
  if (typeof value !== 'string') return value;
  return value.replace(/\$\{\{\s*([\s\S]*?)\s*\}\}/g, (_match, expr) =>
    stringify(evalExpr(expr, ctx))
  );
}

function evalIf(condition, ctx) {
  if (condition == null) return true;
  let text = String(condition).trim();
  const wrapped = text.match(/^\$\{\{\s*([\s\S]*?)\s*\}\}$/);
  if (wrapped) text = wrapped[1].trim();
  if (text === 'success()' || text === 'always()') return !ctx.jobFailed;
  if (text === 'failure()') return Boolean(ctx.jobFailed);
  return truthy(evalExpr(text, ctx));
}

function shellWords(line) {
  const words = [];
  let current = '';
  let quote = null;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (quote) {
      if (char === quote) quote = null;
      else current += char;
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (/\s/.test(char)) {
      if (current) words.push(current);
      current = '';
      continue;
    }
    current += char;
  }
  if (current) words.push(current);
  return words;
}

// Same path rule as cli/cli v2.105.0 pkg/cmd/issue/shared/lookup.go tryParseIssueFromURL.
function parseIssueArg(arg) {
  try {
    const url = new URL(arg);
    const match = url.pathname.match(
      /^\/([^/]+)\/([^/]+)\/(?:issues|pull)\/(\d+)/
    );
    if (match) return Number(match[3]);
  } catch {
    // not a URL; fall through to a bare number
  }
  const number = Number(String(arg).replace(/^#/, ''));
  if (!Number.isInteger(number)) {
    throw new Error(`invalid issue format: ${JSON.stringify(arg)}`);
  }
  return number;
}

class ExerciseRepo {
  constructor() {
    this.issues = [];
    this.comments = [];
    this.closes = [];
    this.readmeIssueUrls = [];
    this.variables = {};
    this.nextNumber = 1;
    this.nextCommentId = 1;
    this.clock = Date.parse('2026-01-01T00:00:00Z');
    this.actor = ACTIONS_BOT;
  }

  addIssue({
    title,
    body = '',
    user = this.actor,
    state = 'open',
    pullRequest = false,
    labels = [],
    createdAt = null,
  }) {
    const number = this.nextNumber++;
    const created = createdAt ?? new Date(this.clock).toISOString();
    this.clock += 60 * 1000;
    const issue = {
      number,
      title,
      body,
      state,
      user: { login: user },
      created_at: created,
      updated_at: created,
      html_url: `https://github.com/${OWNER}/${REPO}/${
        pullRequest ? 'pull' : 'issues'
      }/${number}`,
      labels: labels.map((name) => ({ name })),
    };
    if (pullRequest) {
      issue.pull_request = {
        url: `https://api.github.com/repos/${OWNER}/${REPO}/pulls/${number}`,
        html_url: issue.html_url,
        diff_url: `${issue.html_url}.diff`,
        patch_url: `${issue.html_url}.patch`,
      };
    }
    this.issues.push(issue);
    return issue;
  }

  listForRepo(params) {
    let items = this.issues.slice();
    const state = params.state ?? 'open';
    if (state !== 'all') items = items.filter((issue) => issue.state === state);
    if (params.creator) {
      items = items.filter((issue) => issue.user.login === params.creator);
    }
    if (params.labels) {
      const wanted = String(params.labels)
        .split(',')
        .map((label) => label.trim())
        .filter(Boolean);
      items = items.filter((issue) =>
        wanted.every((label) =>
          issue.labels.some((item) => item.name === label)
        )
      );
    }
    const sort = params.sort ?? 'created';
    const direction = params.direction ?? 'desc';
    const field = sort === 'updated' ? 'updated_at' : 'created_at';
    items.sort((a, b) => {
      const cmp = String(a[field]).localeCompare(String(b[field]));
      return direction === 'asc' ? cmp : -cmp;
    });
    const perPage = Number(params.per_page ?? 30);
    const page = Number(params.page ?? 1);
    const start = (page - 1) * perPage;
    return items.slice(start, start + perPage);
  }

  commentOn(issueNumber, via, body = '') {
    const issue = this.requireIssue(issueNumber);
    const comment = {
      id: this.nextCommentId++,
      issue_number: issue.number,
      body,
      via,
      user: { login: this.actor },
    };
    this.comments.push(comment);
    return comment;
  }

  closeIssue(issueNumber, via) {
    const issue = this.requireIssue(issueNumber);
    if (issue.state !== 'closed') issue.state = 'closed';
    this.closes.push({ issue_number: issue.number, via });
    return issue;
  }

  addLabel(issueNumber, label) {
    const issue = this.requireIssue(issueNumber);
    if (!issue.labels.some((item) => item.name === label)) {
      issue.labels.push({ name: label });
    }
  }

  requireIssue(issueNumber) {
    const issue = this.issues.find(
      (item) => item.number === Number(issueNumber)
    );
    if (!issue) throw new Error(`issue ${issueNumber} does not exist`);
    return issue;
  }

  githubClient() {
    const repo = this;
    const response = (data, status = 200) => ({
      status,
      url: `https://api.github.com/repos/${OWNER}/${REPO}`,
      headers: {},
      data,
    });
    return {
      rest: {
        issues: {
          async listForRepo(params) {
            return response(repo.listForRepo(params));
          },
          async get(params) {
            return response(repo.requireIssue(params.issue_number));
          },
          async create(params) {
            return response(
              repo.addIssue({ title: params.title, body: params.body ?? '' }),
              201
            );
          },
          async createComment(params) {
            return response(
              repo.commentOn(
                params.issue_number,
                'issues.createComment',
                params.body ?? ''
              ),
              201
            );
          },
          async updateComment(params) {
            const comment = repo.comments.find(
              (item) => item.id === Number(params.comment_id)
            );
            if (!comment) throw new Error(`comment ${params.comment_id} was not found`);
            if (params.body != null) comment.body = params.body;
            return response(comment);
          },
          async update(params) {
            const issue = repo.requireIssue(params.issue_number);
            if (params.state === 'closed') repo.closeIssue(issue.number, 'issues.update');
            if (params.state === 'open') issue.state = 'open';
            if (params.title) issue.title = params.title;
            if (params.body != null) issue.body = params.body;
            return response(issue);
          },
          async addLabels(params) {
            for (const label of params.labels ?? []) {
              repo.addLabel(params.issue_number, label);
            }
            return response(repo.requireIssue(params.issue_number).labels);
          },
          async listComments(params) {
            const comments = repo.comments.filter(
              (item) => item.issue_number === Number(params.issue_number)
            );
            return response(comments);
          },
        },
        repos: {
          async get() {
            return response({
              full_name: `${OWNER}/${REPO}`,
              template_repository: null,
            });
          },
        },
        actions: {
          async createRepoVariable(params) {
            repo.variables[params.name] = String(params.value);
            return response({}, 201);
          },
          async updateRepoVariable(params) {
            repo.variables[params.name] = String(params.value);
            return response({});
          },
          async getRepoVariable(params) {
            if (!Object.prototype.hasOwnProperty.call(repo.variables, params.name)) {
              throw new Error(`variable ${params.name} is not set`);
            }
            return response({
              name: params.name,
              value: repo.variables[params.name],
            });
          },
        },
      },
    };
  }
}

function makeContext(extra = {}) {
  return {
    jobFailed: false,
    github: {
      repository: `${OWNER}/${REPO}`,
      actor: 'student',
      token: TOKEN_PLACEHOLDER,
      ref: 'refs/heads/accelerate-with-copilot',
      ref_name: 'accelerate-with-copilot',
      workflow: extra.workflow ?? 'exercise',
      event: { repository: { is_template: false } },
    },
    secrets: { GITHUB_TOKEN: TOKEN_PLACEHOLDER },
    vars: extra.vars ?? {},
    needs: extra.needs ?? {},
    steps: {},
    inputs: extra.inputs ?? {},
    env: {},
  };
}

async function runScript(script, ctx, repo) {
  const AsyncFunction = Object.getPrototypeOf(async () => null).constructor;
  const previous = new Map();
  const env = ctx.env ?? {};
  for (const [key, value] of Object.entries(env)) {
    previous.set(key, Object.prototype.hasOwnProperty.call(process.env, key) ? process.env[key] : undefined);
    process.env[key] = stringify(value);
  }
  const outputs = {};
  const core = {
    setOutput(name, value) {
      outputs[name] = stringify(value);
    },
    info() {},
    debug() {},
    warning() {},
    startGroup() {},
    endGroup() {},
    // @actions/core setFailed records the failure and does not throw.
    setFailed(message) {
      outputs.__failed = stringify(message);
    },
    getInput(name) {
      return stringify(ctx.inputs?.[name] ?? '');
    },
  };
  const context = {
    repo: { owner: OWNER, repo: REPO },
    payload: ctx.github.event,
  };
  const github = repo.githubClient();
  try {
    const fn = new AsyncFunction(
      'require',
      'github',
      'octokit',
      'context',
      'core',
      'exec',
      'glob',
      'io',
      script
    );
    await fn(
      () => {
        throw new Error('require is not part of this issue-binding path');
      },
      github,
      github,
      context,
      core,
      {},
      {},
      {}
    );
  } finally {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
  return outputs;
}

function expandShellEnv(word, env) {
  return word.replace(/\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g, (_all, braced, bare) => {
    const name = braced || bare;
    return stringify(env[name] ?? '');
  });
}

function runShell(script, ctx, repo) {
  const joined = script.replace(/\\\r?\n/g, ' ');
  for (const rawLine of joined.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    if (/^exit\s+1$/.test(line)) {
      ctx.jobFailed = true;
      continue;
    }
    const words = shellWords(interpolate(line, ctx)).map((word) =>
      expandShellEnv(word, ctx.env)
    );
    if (words[0] !== 'gh') continue;
    if (words[1] === 'workflow') continue;
    if (words[1] === 'variable' && words[2] === 'set') {
      const name = words[3];
      const bodyAt = words.indexOf('--body');
      if (bodyAt === -1 || !name) {
        throw new Error(`unsupported gh variable command: ${line}`);
      }
      repo.variables[name] = words[bodyAt + 1];
      continue;
    }
    if (words[1] === 'issue' && words[2] === 'comment') {
      repo.commentOn(parseIssueArg(words[3]), 'gh issue comment');
      continue;
    }
    if (words[1] === 'issue' && words[2] === 'close') {
      repo.closeIssue(parseIssueArg(words[3]), 'gh issue close');
      continue;
    }
    if (words[1] === 'issue' && words[2] === 'edit') {
      const number = parseIssueArg(words[3]);
      for (let i = 4; i < words.length; i++) {
        if (words[i] === '--add-label') repo.addLabel(number, words[++i]);
      }
      continue;
    }
    throw new Error(`unsupported gh command in exercise workflow: ${line}`);
  }
}

function workflowInputs(doc, provided, ctx) {
  const defined = doc.on?.workflow_call?.inputs ?? {};
  const inputs = {};
  for (const [name, spec] of Object.entries(defined)) {
    if (Object.prototype.hasOwnProperty.call(provided, name)) {
      inputs[name] = interpolate(provided[name], ctx);
    } else if (spec && Object.prototype.hasOwnProperty.call(spec, 'default')) {
      inputs[name] = spec.default;
    } else {
      inputs[name] = '';
    }
  }
  for (const [name, value] of Object.entries(provided)) {
    if (!Object.prototype.hasOwnProperty.call(inputs, name)) {
      inputs[name] = interpolate(value, ctx);
    }
  }
  return inputs;
}

function jobNeeds(job) {
  if (job.needs == null) return [];
  return Array.isArray(job.needs) ? job.needs : [job.needs];
}

function orderJobs(jobs) {
  const names = Object.keys(jobs);
  const done = new Set();
  const ordered = [];
  function visit(name, stack) {
    if (done.has(name)) return;
    if (stack.has(name)) throw new Error(`job cycle at ${name}`);
    stack.add(name);
    for (const need of jobNeeds(jobs[name])) visit(need, stack);
    stack.delete(name);
    done.add(name);
    ordered.push(name);
  }
  for (const name of names) visit(name, new Set());
  return ordered;
}

function usesFixture(uses) {
  if (typeof uses !== 'string') return null;
  if (uses.includes('find-exercise-issue.yml')) return 'find-exercise-issue.yml';
  if (uses.includes('start-exercise.yml')) return 'start-exercise.yml';
  if (uses.includes('finish-exercise.yml')) return 'finish-exercise.yml';
  return null;
}

async function runWorkflowDocument(doc, repo, parentCtx) {
  const jobs = doc.jobs ?? {};
  const results = {};
    for (const name of orderJobs(jobs)) {
    const job = jobs[name];
    const needed = jobNeeds(job);
    if (needed.some((need) => results[need]?.failed)) {
      results[name] = { outputs: {}, skipped: true, failed: true };
      continue;
    }
    const needs = {};
    for (const need of needed) {
      needs[need] = { outputs: results[need]?.outputs ?? {} };
    }
    const ctx = makeContext({
      workflow: doc.name,
      vars: repo.variables,
      needs,
      inputs: parentCtx.inputs,
    });
    ctx.jobFailed = false;
    if (!evalIf(job.if, ctx)) {
      results[name] = { outputs: {}, skipped: true };
      continue;
    }
    if (job.uses) {
      const fixture = usesFixture(job.uses);
      if (!fixture) {
        throw new Error(`workflow calls ${job.uses}, which this harness does not execute`);
      }
      const called = loadYaml(join(fixtureDir, fixture));
      const inputs = workflowInputs(called, job.with ?? {}, ctx);
      const calledResults = await runWorkflowDocument(called, repo, { inputs });
      const outputs = {};
      const outputCtx = {
        ...ctx,
        jobs: Object.fromEntries(
          Object.entries(calledResults).map(([jobName, result]) => [
            jobName,
            { outputs: result.outputs },
          ])
        ),
      };
      for (const [outputName, spec] of Object.entries(called.on?.workflow_call?.outputs ?? {})) {
        outputs[outputName] = stringify(interpolate(spec.value, outputCtx));
      }
      results[name] = { outputs };
      continue;
    }
    const jobEnv = {};
    for (const [key, value] of Object.entries(doc.env ?? {})) {
      jobEnv[key] = interpolate(value, ctx);
    }
    for (const [key, value] of Object.entries(job.env ?? {})) {
      jobEnv[key] = interpolate(value, ctx);
    }
    ctx.env = jobEnv;
    const steps = Array.isArray(job.steps) ? job.steps : [];
    for (const step of steps) {
      const stepCtx = {
        ...ctx,
        env: { ...jobEnv },
        steps: ctx.steps,
      };
      if (!evalIf(step.if, stepCtx)) continue;
      for (const [key, value] of Object.entries(step.env ?? {})) {
        stepCtx.env[key] = interpolate(value, stepCtx);
      }
      ctx.env = stepCtx.env;
      const uses = step.uses ?? '';
      if (uses.includes('actions/github-script')) {
        const script = interpolate(step.with?.script ?? '', stepCtx);
        let outputs = {};
        let thrown = false;
        try {
          outputs = await runScript(script, stepCtx, repo);
        } catch (error) {
          thrown = true;
          outputs = { __failed: error.message };
        }
        const failed = thrown || Object.prototype.hasOwnProperty.call(outputs, '__failed');
        delete outputs.__failed;
        if (step.id) {
          ctx.steps[step.id] = { outputs, outcome: failed ? 'failure' : 'success' };
        }
        if (failed && !step['continue-on-error']) {
          ctx.jobFailed = true;
          break;
        }
        continue;
      }
      if (uses.includes('GrantBirki/comment')) {
        const issueNumber = interpolate(step.with?.['issue-number'] ?? '', stepCtx);
        const commentId = interpolate(step.with?.['comment-id'] ?? '', stepCtx);
        if (commentId) {
          const existing = repo.comments.find((item) => String(item.id) === commentId);
          if (existing) {
            existing.body = `${existing.body}\nupdated`;
            existing.via = 'GrantBirki/comment update';
          } else {
            repo.commentOn(issueNumber, 'GrantBirki/comment', 'rendered comment');
          }
        } else {
          repo.commentOn(issueNumber, 'GrantBirki/comment', 'rendered comment');
        }
        continue;
      }
      if (uses.includes('peter-evans/find-comment')) {
        const issueNumber = Number(
          interpolate(step.with?.['issue-number'] ?? '', stepCtx)
        );
        const direction = interpolate(step.with?.direction ?? 'first', stepCtx);
        const matches = repo.comments.filter((item) => item.issue_number === issueNumber);
        const chosen = direction === 'last' ? matches[matches.length - 1] : matches[0];
        if (step.id) {
          ctx.steps[step.id] = {
            outputs: { 'comment-id': chosen ? String(chosen.id) : '' },
          };
        }
        continue;
      }
      if (uses.includes('action-text-variables')) {
        const vars = interpolate(step.with?.['template-vars'] ?? '', stepCtx);
        const url = vars.match(/issue_url:\s*(\S+)/);
        if (url) repo.readmeIssueUrls.push(url[1]);
        if (step.id) {
          ctx.steps[step.id] = { outputs: { 'updated-text': 'exercise-message' } };
        }
        continue;
      }
      if (step.run) {
        runShell(String(step.run), stepCtx, repo);
      }
    }
    const outputs = {};
    for (const [outputName, expression] of Object.entries(job.outputs ?? {})) {
      outputs[outputName] = stringify(interpolate(expression, ctx));
    }
    results[name] = { outputs, failed: ctx.jobFailed };
  }
  return results;
}

async function runWorkflowFile(file, repo) {
  const doc = loadYaml(file);
  return runWorkflowDocument(doc, repo, { inputs: {} });
}

function plantedIssues(repo, startIssue) {
  for (let index = 0; index < 5; index++) {
    repo.addIssue({
      title: `Question ${index} about the lesson`,
      user: 'reader',
    });
  }
  const attacker = repo.addIssue({
    title: 'Exercise: hijack',
    user: 'attacker',
    body: 'Opened by a user who is not the start workflow.',
  });
  const pullRequest = repo.addIssue({
    title: 'Exercise: pull request',
    user: 'attacker',
    pullRequest: true,
  });
  assert.ok(
    startIssue.created_at < attacker.created_at &&
      attacker.created_at < pullRequest.created_at
  );
  assert.notEqual(attacker.user.login, ACTIONS_BOT);
  return { attacker, pullRequest };
}

function assertBoundToStart(repo, startIssue, attacker, workflowFile) {
  const commentTargets = repo.comments.map((comment) => comment.issue_number);
  const closeTargets = repo.closes.map((close) => close.issue_number);
  const detail = [
    `${workflowFile} comments=${commentTargets.join(',') || 'none'}`,
    `closes=${closeTargets.join(',') || 'none'}`,
    `readme=${repo.readmeIssueUrls.join(',') || 'none'}`,
    `start=#${startIssue.number} (${startIssue.title})`,
    `attacker=#${attacker.number} (${attacker.title}) state=${attacker.state}`,
  ].join('; ');
  assert.ok(commentTargets.length > 0, `no comments were posted; ${detail}`);
  assert.ok(
    commentTargets.every((number) => number === startIssue.number),
    `comments were not bound to the start issue; ${detail}`
  );
  assert.ok(
    closeTargets.every((number) => number === startIssue.number),
    `close was not bound to the start issue; ${detail}`
  );
  assert.ok(
    repo.readmeIssueUrls.every((url) => url === startIssue.html_url),
    `README issue URL was not the start issue; ${detail}`
  );
  assert.equal(attacker.state, 'open', detail);
  assert.equal(
    repo.comments.filter((comment) => comment.issue_number === attacker.number).length,
    0,
    detail
  );
  if (workflowFile === '5-step.yml') {
    assert.ok(closeTargets.includes(startIssue.number), detail);
    assert.equal(startIssue.state, 'closed', detail);
  }
}

test('listForRepo stand-in orders created desc newest first and keeps pull requests', () => {
  const repo = new ExerciseRepo();
  const older = repo.addIssue({ title: 'older', createdAt: '2020-01-01T00:00:00Z' });
  const newer = repo.addIssue({ title: 'newer', createdAt: '2024-01-01T00:00:00Z' });
  const pull = repo.addIssue({
    title: 'pull',
    createdAt: '2025-01-01T00:00:00Z',
    pullRequest: true,
  });
  const listed = repo.listForRepo({
    state: 'all',
    sort: 'created',
    direction: 'desc',
    per_page: 50,
  });
  assert.deepEqual(
    listed.map((issue) => issue.number),
    [pull.number, newer.number, older.number]
  );
  assert.equal(Boolean(listed[0].pull_request), true);
  assert.equal(listed[1].pull_request, undefined);
  const ascending = repo.listForRepo({
    state: 'all',
    sort: 'created',
    direction: 'asc',
    per_page: 1,
  });
  assert.equal(ascending[0].number, older.number);
});

for (const workflowFile of STEP_WORKFLOWS) {
  test(`${workflowFile} comments and closes only the issue created by start`, async () => {
    const repo = new ExerciseRepo();
    await runWorkflowFile(join(workflowsDir, '0-start-exercise.yml'), repo);
    const created = repo.issues.filter((issue) => issue.user.login === ACTIONS_BOT);
    assert.equal(
      created.length,
      1,
      `start workflow created ${created.length} issues`
    );
    const startIssue = created[0];
    assert.match(startIssue.title, /Exercise/);
    const { attacker } = plantedIssues(repo, startIssue);

    repo.comments = [];
    repo.closes = [];
    repo.readmeIssueUrls = [];
    await runWorkflowFile(join(workflowsDir, workflowFile), repo);
    assertBoundToStart(repo, startIssue, attacker, workflowFile);
  });
}

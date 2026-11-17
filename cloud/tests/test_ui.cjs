const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const pane = { textContent: '', innerHTML: '', appendChild(x) { this.retry = x; }, dataset: {pane: 'instruct'}, classList: {toggle(){}} };
const tab = {dataset: {tab: 'instruct'}, classList: {toggle(){}}};
const nodes = {'[data-pane="instruct"]': pane};
let response = {ok: true, rules: {coding: ['Код <test>'], talk: ['Общение']}};
const context = vm.createContext({
  console, localStorage: {getItem(){return null;}}, window: {},
  document: {addEventListener(){}, querySelector(s){return nodes[s] || (nodes[s] = {addEventListener(){}, classList: {toggle(){}}, style: {}});},
    querySelectorAll(s){return s === '.mpane' ? [pane] : s === '.mtabs button[data-tab]' ? [tab] : [];},
    createElement(){return {}; }},
  fetch: async () => ({status: 200, headers: {get(){return 'application/json';}}, json: async () => response}),
  FormData: class {}, setTimeout, clearTimeout, setInterval, clearInterval,
});
vm.runInContext(fs.readFileSync(__dirname + '/../web/app.js', 'utf8'), context);
(async () => {
  vm.runInContext("renderTab('instruct')", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(pane.innerHTML, /Код &lt;test&gt;/);
  assert.match(pane.innerHTML, /Общение/);
  response = {ok: false, error: 'offline'};
  await vm.runInContext('tabInstruct()', context);
  assert.match(pane.textContent, /offline/);
  assert.equal(pane.retry.textContent, 'Повторить');
  // Late status from a completed request must not recreate the typing row.
  vm.runInContext('typingOn = function(s) { window.lastStatus = s; }; typingOff = function() {};', context);
  let resolveFetch;
  context.fetch = () => new Promise(resolve => {resolveFetch = resolve;});
  vm.runInContext("typingStart('request_ui')", context);
  await new Promise(resolve => setTimeout(resolve, 350));
  vm.runInContext('typingStop()', context);
  resolveFetch({status: 200, headers: {get(){return 'application/json';}}, json: async () => ({ok: true, status: 'late'})});
  await new Promise(resolve => setImmediate(resolve));
  assert.notEqual(context.window.lastStatus, 'late');
  console.log('UI checks passed: instruction routing, escaping, retry, stale status.');
})().catch(e => {console.error(e); process.exitCode = 1;});

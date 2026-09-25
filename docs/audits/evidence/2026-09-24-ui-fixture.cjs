// Extract the actual card JSX/helpers; render offline fixtures, not production pages.
// Run from repository root: node docs/audits/evidence/2026-09-24-ui-fixture.cjs
// Writes only /tmp/stockai-ui-review-20260924/cards.html. Requires frontend dependencies.
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '../../..');
const frontend = path.join(root, 'frontend');
const ts = require(require.resolve('typescript', {paths:[frontend]}));
const esbuild = require(require.resolve('esbuild', {paths:[frontend]}));
function source(rel) {
  const text = fs.readFileSync(path.join(frontend, rel), 'utf8');
  return ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}
function declarations(src, names) {
  return src.statements.filter(n =>
    (ts.isFunctionDeclaration(n) && names.includes(n.name?.text)) ||
    (ts.isVariableStatement(n) && n.declarationList.declarations.some(d => names.includes(d.name.getText(src))))
  ).map(n=>n.getText(src)).join('\n');
}
const income = source('src/pages/options-income.tsx');
const portfolio = source('src/pages/portfolio.tsx');
const candidates = [];
function visit(n) {
  if (ts.isJsxElement(n)) {
    const t=n.getText(portfolio);
    if (t.includes('metricCard(m.color)') && t.includes('result.expected_return')) candidates.push(t);
  }
  ts.forEachChild(n, visit);
}
visit(portfolio);
const metrics = candidates.sort((a,b)=>a.length-b.length)[0];
if (!metrics) throw Error('Actual metrics row not found');
const helpers = declarations(income, ['fmtUSD','fmtDate','STRATEGY_LABEL','STRATEGY_COLOR','StrategyBadge','CARD','ScorePill','TopPicks']);
const metricHelpers = declarations(portfolio, ['metricCard','fmt']);
const code = `
import React from 'react';
import {createRoot} from 'react-dom/client';
${helpers}
${metricHelpers}
function Metrics({result}) { const accentColor='#f472b6'; return (${metrics}); }
const base = {symbol:'FIXTURE PUT',strategy:'CASH_SECURED_PUT',option_symbol:'offline-put',
  quality_score:75,strike:90,expiry:'2026-09-25',days_to_expiry:1,premium_per_contract:100,
  annualized_yield_pct:12.17,annualized_yield_on_collateral_pct:13.52,yield_denominator:'strike',
  otm_cushion_pct:10,open_interest:500,collateral_required:9000};
// Yield values demonstrate the audit's 30-DTE example; use 30 consistently in the card.
base.days_to_expiry=30;
const call={...base,symbol:'FIXTURE CALL',strategy:'COVERED_CALL',option_symbol:'offline-call',
  strike:100,collateral_required:10000,annualized_yield_on_collateral_pct:12.17,yield_denominator:'underlying_price'};
const legacy={...base,symbol:'LEGACY RESPONSE',option_symbol:'offline-legacy',annualized_yield_on_collateral_pct:undefined};
const result={expected_return:.076,expected_vol:.19,sharpe_ratio:.189,max_drawdown:-.20,diversification:0};
createRoot(document.getElementById('root')).render(<main>
  <h1>Audit fixtures — actual card source</h1>
  <p>Synthetic data. No production API, account, or order connection.</p>
  <h2>Portfolio · 5% cash</h2><Metrics result={result}/>
  <h2>Options · separate yield definitions</h2><TopPicks candidates={[base,call,legacy]}/>
  <p id="date-evidence">Input expiry: 2026-09-25 · rendered: {fmtDate('2026-09-25')} · zone: {Intl.DateTimeFormat().resolvedOptions().timeZone}</p>
</main>);
setTimeout(()=>{
 const result={timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,input_expiry:'2026-09-25',
   formatted_expiry:fmtDate('2026-09-25'),width:innerWidth,
   horizontal_overflow:document.documentElement.scrollWidth>innerWidth,
   collateral_label_present:document.body.innerText.includes('13.5% ann. on collateral'),
   spot_label_present:document.body.innerText.includes('12.2% ann. on spot'),
   cash_hint_present:document.body.innerText.toLowerCase().includes('incl. cash')};
 const el=document.createElement('pre');el.id='audit-result';el.textContent=JSON.stringify(result);document.body.appendChild(el);
},200);
`;
const js=esbuild.buildSync({stdin:{contents:code,resolveDir:frontend,loader:'tsx'},bundle:true,write:false,minify:true,platform:'browser'}).outputFiles[0].text;
const html=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline audit card fixtures</title><style>body{background:#0b1220;color:#cbd5e1;font:14px Arial;margin:0}main{padding:24px;max-width:1160px;margin:auto}h1{font-size:23px;color:#f1f5f9}h2{font-size:16px;margin-top:28px}p{line-height:1.6;color:#94a3b8}pre{white-space:pre-wrap;font-size:11px;padding:24px;color:#94a3b8}</style></head><body><div id="root"></div><script>${js.replace(/<\/script/gi,'<\\/script')}</script></body></html>`;
const output='/tmp/stockai-ui-review-20260924/cards.html';
fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,html);
console.log(output);

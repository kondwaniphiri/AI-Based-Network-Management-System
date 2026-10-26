// Browser-independent interaction checks. No fabricated data is added to the app.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../server/static/dashboard.js'),'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

async function setup({withoutChart=false}={}) {
  class Element {
    constructor() {this.dataset={};this.attributes={};this.listeners={};this.hidden=false;this.children=[];this.disabled=false;}
    setAttribute(k,v) {this.attributes[k]=v;}
    addEventListener(k,v) {this.listeners[k]=v;}
    replaceChildren(...items) {this.children=items;}
    append(...items) {this.children.push(...items);}
    click() {this.listeners.click?.();}
  }
  const elements = {}, tabs = ['traffic','rtt','loss'].map(key => {const e=new Element();e.dataset.chart=key;return e;});
  const get = id => elements[id] ||= new Element();
  const scheduled = [], charts = [], requests=[];
  const data = {
    run_id:'pilot & 01',server_time:1700000000,uptime_s:120,
    devices:{esp32_1:{label:'ESP32 1',ip:'192.168.8.101',protocols:['HTTP','UDP'],status:'online',http_count:4,udp_count:6,total_bytes:800,latency_ms:4,packet_loss_pct:0},
      esp32_2:{label:'ESP32 2',ip:'192.168.8.102',protocols:['HTTP'],status:'offline',http_count:0,udp_count:0,total_bytes:0,latency_ms:null,packet_loss_pct:null}},
    phones:{},totals:{total_bytes:800,dropped_packets:0,current_drop_rate_pct:0},
    timeseries:{labels:['22:13:19','22:13:20'],series:{esp32_1_http:[1,2],esp32_1_udp:[3,4],esp32_2_http:[0,0]}},
    gateway:{status:'unavailable',agent:null},recent_events:[]};
  const state = {data,fail:false};
  const context={document:{getElementById:get,querySelectorAll:()=>tabs,createElement:()=>new Element()},
    location:{host:'localhost:5000'},AbortSignal:{timeout:()=>undefined},
    setTimeout:fn=>{scheduled.push(fn);return scheduled.length;},clearTimeout:()=>{},
    fetch:async url=>{requests.push(url);if(state.fail)throw Error('offline');return {ok:true,status:url==='/api/analysis'?404:200,json:async()=>state.data};}};
  if(!withoutChart)context.Chart=class {constructor(element,config){Object.assign(this,config);charts.push(this);}update(){}toBase64Image(){return 'data:image/png;base64,test';}};
  vm.runInNewContext(script,context);
  await flush();
  return {get,tabs,scheduled,charts,state,requests};
}

test('renders actual measurements, run-filtered exports and unavailable gateway',async()=>{
  const app=await setup();
  assert.equal(app.get('total-messages').textContent,'10');
  assert.equal(app.get('gateway-status').textContent,'No agent data');
  assert.equal(app.get('gateway-in').textContent,'—');
  assert.match(app.get('device-rows').innerHTML,/4\.0 ms/);
  assert.match(app.get('export-events').href,/run_id=pilot%20%26%2001/);
  assert.deepEqual(Array.from(app.charts[0].data.datasets[0].data),[4,6]);
});

test('pause stops polling; resume refreshes; stale connection retains last values',async()=>{
  const app=await setup();
  app.get('pause').click();
  assert.equal(app.get('pause').attributes['aria-pressed'],'true');
  const requests=app.requests.length;
  await app.scheduled.at(-1)();
  assert.equal(app.requests.length,requests);
  app.get('pause').click();await flush();
  assert.ok(app.requests.length>requests);
  app.state.fail=true;await app.scheduled.at(-1)();
  assert.equal(app.get('connection').dataset.state,'error');
  assert.equal(app.get('connection-error').hidden,false);
  assert.equal(app.get('total-messages').textContent,'10');
});

test('RTT and delivery tabs keep unmeasured nodes missing',async()=>{
  const app=await setup();
  app.tabs[1].click();
  assert.equal(app.get('traffic-title').textContent,'Round-trip time');
  assert.equal(app.charts[0].data.datasets[1].data[0],null);
  app.tabs[2].click();
  assert.equal(app.charts[0].data.datasets[0].data[0],0);
  assert.equal(app.charts[0].data.datasets[1].data[0],null);
});

test('untrusted labels are escaped and impairment is explicitly labelled',async()=>{
  const app=await setup();
  app.state.data.devices.esp32_1.label='<img src=x onerror=alert(1)>';
  app.state.data.totals.current_drop_rate_pct=5;
  await app.scheduled.at(-1)();
  assert.doesNotMatch(app.get('device-rows').innerHTML,/<img/);
  assert.match(app.get('device-rows').innerHTML,/&lt;img/);
  assert.equal(app.get('impairment-notice').hidden,false);
  assert.match(app.get('impairment-notice').textContent,/5\.0%/);
});

test('failed/stale SNMP rates are withheld and missing archive is handled',async()=>{
  const app=await setup();
  app.state.data.gateway={status:'stale',timestamp:1699999900,agent:'router',in_bps:10000,out_bps:20000};
  await app.scheduled.at(-1)();
  assert.equal(app.get('gateway-status').textContent,'Stale sample');
  assert.equal(app.get('gateway-in').textContent,'—');
  app.get('archive').open=true;app.get('archive').listeners.toggle();await flush();
  assert.equal(app.get('archive-status').textContent,'No saved experiment plots available.');
});

test('missing Chart library leaves the table and polling usable',async()=>{
  const app=await setup({withoutChart:true});
  assert.equal(app.get('traffic-chart').hidden,true);
  assert.equal(app.get('chart-unavailable').hidden,false);
  assert.match(app.get('device-rows').innerHTML,/ESP32 1/);
  assert.equal(app.get('connection').dataset.state,'live');
});

test('gateway mode labels forwarded HTTP RTT and NAT client grouping honestly',async()=>{
  const app=await setup();
  app.state.data.gateway_mode=true;
  app.state.data.latency_kind='http_rtt';
  app.state.data.phones={'192.168.8.110':{status:'online',request_count:3,bytes_served:1024}};
  await app.scheduled.at(-1)();
  assert.equal(app.get('rtt-heading').textContent,'HTTP RTT');
  assert.equal(app.get('online-label').textContent,'Online node groups');
  assert.match(app.get('device-rows').innerHTML,/NAT client group/);
  app.tabs[1].click();
  assert.match(app.get('chart-subtitle').textContent,/Forwarded HTTP/);
  assert.match(app.get('measurement-note').textContent,/aggregated/);
});

/* Live measurements only. Presentation state stays separate from server data. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const colors = ['#32685a', '#b18543', '#527995', '#866b8a'];
  const history = [];
  let snapshot = null, chart = null, mode = 'traffic', paused = false, busy = false;
  let timer = null, lastServerTime = null, archiveLoaded = false;
  const number = n => typeof n === 'number' && Number.isFinite(n);
  const count = n => number(n) ? new Intl.NumberFormat().format(n) : '—';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const unit = (value, units, base) => {
    if (!number(value)) return '—';
    let n = value, i = 0;
    while (Math.abs(n) >= base && i < units.length - 1) { n /= base; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
  };
  const bytes = n => unit(n, ['B', 'KiB', 'MiB', 'GiB'], 1024);
  const rate = n => unit(n, ['bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s'], 1000);
  const measurement = (n, suffix) => number(n) ? `${n.toFixed(1)}${suffix}` : '—';
  const utcTime = t => new Date(t * 1000).toLocaleTimeString('en-GB', {timeZone:'UTC', hour12:false});
  const status = value => ['online','idle','offline'].includes(value) ? value : 'offline';
  function connection(state, label) {
    el('connection').dataset.state = state;
    el('connection-label').textContent = label;
  }
  function table(data) {
    const devices = Object.entries(data.devices || {}), phones = Object.entries(data.phones || {});
    const rows = devices.map(([key, d]) => {
      const state = status(d.status), transport = Array.isArray(d.protocols) ? d.protocols.join(' + ') : d.protocols;
      return `<tr><td><div class="node-name">${esc(d.label || key)}</div><div class="node-role">Sensor node</div></td><td class="mono">${esc(d.ip)}</td><td><span class="status ${state}">${state[0].toUpperCase()+state.slice(1)}</span></td><td>${esc(transport || '—')}</td><td class="numeric">${count((d.http_count || 0)+(d.udp_count || 0))}</td><td class="numeric">${bytes(d.total_bytes)}</td><td class="numeric">${state === 'online' ? measurement(d.latency_ms, ' ms') : '—'}</td><td class="numeric">${measurement(d.packet_loss_pct, '%')}</td></tr>`;
    });
    phones.forEach(([ip, p]) => {
      const state = status(p.status);
      rows.push(`<tr><td><div class="node-name">${data.gateway_mode ? 'NAT client group' : 'Client '+esc(ip.split('.').pop())}</div><div class="node-role">${esc(p.last_url || 'Browser client')}</div></td><td class="mono">${esc(ip)}</td><td><span class="status ${state}">${state[0].toUpperCase()+state.slice(1)}</span></td><td>${esc(p.protocols || 'HTTP')}</td><td class="numeric">${count(p.request_count)}</td><td class="numeric">${bytes(p.bytes_served)}</td><td class="numeric">—</td><td class="numeric">—</td></tr>`);
    });
    el('device-rows').innerHTML = rows.join('') || '<tr><td colspan="8" class="empty-row">No devices configured.</td></tr>';
    el('devices-count').textContent = `${devices.length} sensor nodes · ${phones.length} ${data.gateway_mode ? 'client groups' : 'clients'} observed`;
    const online = [...devices, ...phones].filter(([,d]) => d.status === 'online').length;
    el('total-online').innerHTML = `${online} <small>/ ${devices.length + phones.length}</small>`;
  }
  function gateway(g = {}) {
    const labels = {ok:'Receiving counters', unavailable:'No agent data', stale:'Stale sample', warmup:'First sample', collection_failed:'Poll failed', counter_reset_or_wrap:'Counter reset'};
    el('gateway-status').textContent = labels[g.status] || 'No agent data';
    el('gateway-status').className = `status ${g.status === 'ok' ? 'online' : g.status === 'unavailable' ? 'offline' : 'idle'}`;
    el('gateway-address').textContent = g.agent || 'Address not configured';
    el('gateway-note').textContent = g.status === 'ok' ? 'Latest counters from the gateway SNMP collector.' : g.status === 'stale' ? 'The collector has not provided a recent sample. Rates are withheld until collection resumes.' : g.status === 'collection_failed' ? 'The latest SNMP request failed. Check gateway agent availability and collector settings.' : g.status === 'warmup' ? 'One counter sample received. A second sample is needed to calculate rates.' : g.status === 'counter_reset_or_wrap' ? 'Counters changed discontinuously. Waiting for the next valid interval.' : 'Router measurements will appear when the gateway SNMP agent and collector are running.';
    el('gateway-sample').textContent = number(g.timestamp) ? `${utcTime(g.timestamp)} UTC` : '—';
    el('gateway-interface').textContent = g.interface ? `ifIndex ${g.interface}` : '—';
    el('gateway-in').textContent = g.status === 'ok' ? rate(g.in_bps) : '—';
    el('gateway-out').textContent = g.status === 'ok' ? rate(g.out_bps) : '—';
    el('gateway-utilization').textContent = g.status === 'ok' ? `${measurement(g.in_utilization_pct, '%')} / ${measurement(g.out_utilization_pct, '%')}` : '—';
  }
  function activity(events) {
    el('activity').innerHTML = (events || []).slice(0,30).map(e => `<li class="event"><time>${esc(e.time)}</time><span class="event-source">${esc(e.source)}</span><span class="event-detail">${esc(e.proto)} · ${esc(e.detail)}</span></li>`).join('') || '<li class="empty">No traffic recorded yet. Activity will appear as devices send data.</li>';
  }
  function renderChart() {
    if (!snapshot) return;
    const meta = {
      traffic: ['Sensor traffic', 'Received messages per second · last 30 seconds · UTC', 'HTTP and UDP messages received from each sensor. Phone stream chunks are excluded.', 'Messages / s'],
      rtt: ['Round-trip time', 'Latest ICMP probe · observed during this browser session · UTC', 'Repeated values represent the latest successful ping, not a new probe every second. Idle and offline devices have no current value.', 'RTT (ms)'],
      loss: ['UDP delivery gaps', 'Cumulative observed sequence gaps · observed during this browser session · UTC', 'Late arrivals can repair gaps. Missing prefixes and tails cannot be inferred. HTTP-only nodes have no UDP measurement.', 'Observed gaps (%)']
    }[mode];
    if (mode === 'rtt' && snapshot.latency_kind === 'http_rtt') {
      meta[1] = 'Forwarded HTTP probe · observed during this browser session · UTC';
      meta[2] = 'Latest HTTP request/response time through the gateway, including device processing. Repeated values represent the latest successful probe.';
    }
    el('traffic-title').textContent = meta[0]; el('chart-subtitle').textContent = meta[1]; el('chart-caption').textContent = meta[2];
    el('traffic-chart').setAttribute('aria-label', meta[0] + ' chart. Current values are available in the device table below.');
    if (typeof Chart === 'undefined') { el('traffic-chart').hidden = true; el('chart-unavailable').hidden = false; return; }
    el('save-chart').disabled = false;
    const nodes = Object.entries(snapshot.devices || {});
    let labels;
    if (mode === 'traffic') labels = snapshot.timeseries?.labels || [];
    else labels = history.map(h => utcTime(h.time));
    const datasets = nodes.map(([key,d],index) => {
      const values = mode === 'traffic' ? labels.map((_,i) => (snapshot.timeseries?.series?.[key+'_http']?.[i] || 0)+(snapshot.timeseries?.series?.[key+'_udp']?.[i] || 0)) : history.map(h => {
        const device = h.devices[key];
        return mode === 'rtt' ? (device?.status === 'online' && number(device.latency_ms) ? device.latency_ms : null) : number(device?.packet_loss_pct) ? device.packet_loss_pct : null;
      });
      return {label:d.label || key, data:values, borderColor:colors[index % colors.length], backgroundColor:colors[index % colors.length], borderWidth:1.8, pointRadius:0, pointHitRadius:9, tension:0, spanGaps:false};
    });
    if (!chart) chart = new Chart(el('traffic-chart'), {type:'line', data:{labels,datasets}, options:{responsive:true,maintainAspectRatio:false,animation:false,interaction:{intersect:false,mode:'index'},plugins:{legend:{position:'bottom',align:'start',labels:{usePointStyle:true,pointStyle:'line',boxWidth:18,padding:20,color:'#657168',font:{size:11}}}},scales:{x:{grid:{display:false},border:{display:false},ticks:{color:'#7b837b',maxTicksLimit:6,maxRotation:0,font:{size:10}}},y:{beginAtZero:true,border:{display:false},grid:{color:'#eef0eb'},ticks:{color:'#7b837b',maxTicksLimit:5,font:{size:10}},title:{display:true,text:meta[3],color:'#7b837b',font:{size:10}}}}}});
    else { chart.data = {labels,datasets}; chart.options.scales.y.title.text = meta[3]; chart.update('none'); }
  }
  function render(data) {
    snapshot = data;
    if (lastServerTime !== null && data.server_time - lastServerTime > 3) history.push({time:data.server_time-0.5,devices:{}});
    if (lastServerTime === null || Math.floor(data.server_time) !== Math.floor(lastServerTime)) history.push({time:data.server_time,devices:data.devices || {}});
    lastServerTime = data.server_time;
    while (history.length > 60) history.shift();
    el('run-id').textContent = data.run_id || 'manual';
    el('online-label').textContent = data.gateway_mode ? 'Online node groups' : 'Online endpoints';
    el('online-note').textContent = data.gateway_mode ? 'Sensors and clients grouped behind NAT' : 'Configured sensors and observed clients';
    el('rtt-heading').textContent = data.latency_kind === 'http_rtt' ? 'HTTP RTT' : 'ICMP RTT';
    el('measurement-note').textContent = 'Sensor traffic is received data; client traffic is served data. ' +
      (data.gateway_mode ? 'Phone clients behind NAT are aggregated by their observed gateway address. ' : '') +
      'UDP gaps are cumulative missing sequence numbers between observed messages, not measured radio loss.';
    const seconds = data.uptime_s || 0;
    el('uptime').textContent = `${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m ${seconds%60}s`;
    el('updated').textContent = `${utcTime(data.server_time)} UTC`;
    el('server-address').textContent = location.host;
    el('total-messages').textContent = count(Object.values(data.devices || {}).reduce((sum,d) => sum+(d.http_count || 0)+(d.udp_count || 0),0));
    el('total-bytes').textContent = bytes(data.totals?.total_bytes);
    el('total-discards').textContent = count(data.totals?.dropped_packets);
    const impaired = (data.totals?.current_drop_rate_pct || 0) > 0;
    el('impairment-notice').hidden = !impaired;
    if (impaired) el('impairment-notice').textContent = `Application impairment enabled: ${data.totals.current_drop_rate_pct.toFixed(1)}% configured UDP discard probability. These discards are separate from physical network loss.`;
    const query = '?run_id='+encodeURIComponent(data.run_id || 'manual');
    el('export-events').href = '/api/telemetry/export'+query; el('delivery-link').href = '/api/delivery'+query;
    table(data); gateway(data.gateway); activity(data.recent_events); renderChart();
  }
  async function poll() {
    if (paused || busy) return;
    busy = true;
    try {
      const response = await fetch('/api/dashboard', {cache:'no-store',signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Server response '+response.status);
      const data = await response.json();
      if (!paused) { render(data); connection('live','Receiving updates'); el('connection-error').hidden = true; }
    } catch (_) { if (!paused) { connection('error','Connection lost'); el('connection-error').hidden = false; } }
    finally { busy = false; if (!paused) timer = setTimeout(poll,1000); }
  }
  el('pause').addEventListener('click', () => {
    paused = !paused; clearTimeout(timer); el('pause').setAttribute('aria-pressed',String(paused));
    el('pause').textContent = paused ? 'Resume updates' : 'Pause updates';
    if (paused) connection('paused','Updates paused');
    else { connection('connecting','Reconnecting'); if (!busy) poll(); }
  });
  document.querySelectorAll('[data-chart]').forEach(button => button.addEventListener('click', () => {
    mode = button.dataset.chart;
    document.querySelectorAll('[data-chart]').forEach(b => b.setAttribute('aria-pressed',String(b === button)));
    renderChart();
  }));
  async function loadPlots() {
    const button = el('reload-plots'); button.disabled = true;
    el('archive-status').textContent = 'Loading saved plots…';
    try {
      const response = await fetch('/api/analysis',{cache:'no-store',signal:AbortSignal.timeout(5000)});
      if (response.status === 404) { el('archive-status').textContent = 'No saved experiment plots available.'; el('saved-plots').replaceChildren(); return; }
      if (!response.ok) throw new Error();
      const data = await response.json();
      const names = {latency:'Latency',packet_loss:'Packet loss',throughput:'Throughput',protocol_comparison:'Protocol comparison',comparison_table:'Comparison table'};
      el('saved-plots').replaceChildren();
      Object.entries(names).forEach(([key,name]) => {
        if (typeof data[key] !== 'string') return;
        const figure=document.createElement('figure'), caption=document.createElement('figcaption'), image=document.createElement('img'), download=document.createElement('a');
        caption.textContent=name; image.alt='Saved experiment: '+name; image.src='data:image/png;base64,'+data[key]; image.loading='lazy';
        download.textContent='Download PNG'; download.download=key+'.png'; download.href=image.src;
        figure.append(caption,image,download); el('saved-plots').append(figure);
      });
      el('archive-status').textContent = 'Saved experiment outputs. These plots do not represent the current live session.';
    } catch (_) { el('archive-status').textContent = 'Saved plots could not be loaded. Try refreshing.'; }
    finally { archiveLoaded = true; button.disabled = false; }
  }
  el('archive').addEventListener('toggle', () => { if (el('archive').open && !archiveLoaded) loadPlots(); });
  el('reload-plots').addEventListener('click',loadPlots);
  el('save-chart').addEventListener('click', () => {
    if (!chart) return;
    const link = document.createElement('a');
    link.download = `network-${mode}.png`; link.href = chart.toBase64Image(); link.click();
  });
  poll();
})();

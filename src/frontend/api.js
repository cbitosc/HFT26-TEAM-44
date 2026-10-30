/* WildWatch data layer: fetch + polling + shared sidebar values. No sample data lives here. */
(function () {
  var WW = (window.WW = {});
  var css = document.createElement('style');
  css.textContent =
    '@keyframes wwshim{0%{background-position:200% 0}100%{background-position:-200% 0}}' +
    'main.wwloading section{position:relative;overflow:hidden}' +
    'main.wwloading section::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,transparent 25%,rgba(255,255,255,.07) 50%,transparent 75%);background-size:200% 100%;animation:wwshim 1.4s linear infinite}' +
    '.wwskel{background:linear-gradient(90deg,#1C2E27 25%,#2A4239 50%,#1C2E27 75%);background-size:200% 100%;animation:wwshim 1.6s linear infinite}';
  document.head.appendChild(css);

  WW.LV = {
    CRITICAL: { bg: '#FF3D6E', fg: '#2B0612' }, HIGH: { bg: '#FF6B5B', fg: '#2A0D09' },
    MEDIUM: { bg: '#F2A93B', fg: '#1E1405' }, LOW: { bg: '#3E5A4F', fg: '#E8EFEA' },
    POACHER: { bg: '#B9A3FF', fg: '#1B1240' }, EMPTY: { bg: '#24392F', fg: '#C9D6CF' }, REVIEW: { bg: '#5B6CFF', fg: '#FFFFFF' }
  };
  WW.fmt = function (x) { return String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, ','); };
  WW.pct = function (v) { return v == null ? 'Not measured' : (Math.round(v * 1000) / 10) + '%'; };
  WW.cap = function (s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; };
  WW.light = function (a) { return a.night ? 'IR night' : 'Day'; };

  WW.api = function (url, opts) {
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.error || j.detail || ('HTTP ' + r.status)); e.status = r.status; e.body = j; throw e; }
        return j;
      });
    });
  };

  /* WW.load(component, {key: {url: string|fn(component), every: ms}}) */
  WW.load = function (c, spec) {
    c._ww = c._ww || { spec: spec, timers: [] };
    c._ww.spec = spec;
    Object.keys(spec).forEach(function (key) {
      WW.refresh(c, key);
      if (spec[key].every) c._ww.timers.push(setInterval(function () { WW.refresh(c, key); }, spec[key].every));
    });
  };
  WW.refresh = function (c, key) {
    var s = c._ww.spec[key], url = typeof s.url === 'function' ? s.url(c) : s.url;
    return WW.api(url).then(function (j) {
      c.setState(function (st) {
        var d = Object.assign({}, st.data), er = Object.assign({}, st.errs);
        d[key] = j; delete er[key];
        return { data: d, errs: er, loaded: true };
      });
    }).catch(function (e) { WW.fail(c, key, (e && e.message) || 'network error'); });
  };
  WW.fail = function (c, key, msg) {
    c.setState(function (st) { var er = Object.assign({}, st.errs); er[key] = msg; return { errs: er, loaded: true }; });
  };
  WW.ok = function (c, key) {
    c.setState(function (st) { var er = Object.assign({}, st.errs); delete er[key]; return { errs: er }; });
  };
  WW.stop = function (c) { if (c._ww) c._ww.timers.forEach(clearInterval); };

  WW.ago = function (s) {
    if (s == null) return 'No images yet';
    if (s < 60) return 'Last image ' + s + ' s ago';
    if (s < 3600) return 'Last image ' + Math.floor(s / 60) + ' min ago';
    if (s < 86400) return 'Last image ' + Math.floor(s / 3600) + ' h ago';
    return 'Last image ' + Math.floor(s / 86400) + ' d ago';
  };

  /* values every page needs: sidebar badge, pipeline box, loading class, API-down banner */
  WW.shell = function (st) {
    var S = st && st.data && st.data.summary;
    return {
      alertCount: S ? S.active_alerts : '–',
      pipeText: S ? S.watcher.status : 'Connecting…',
      pipeColor: S ? (S.watcher.running ? '#5FD39A' : '#9DB0A6') : '#9DB0A6',
      lastImage: S ? WW.ago(S.last_image_ago_s) : ' ',
      mainCls: st && st.loaded ? '' : 'wwloading',
      apiDown: !!(st && st.errs && Object.keys(st.errs).length),
      apiError: st && st.errs && Object.keys(st.errs).length ? st.errs[Object.keys(st.errs)[0]] : ''
    };
  };
})();

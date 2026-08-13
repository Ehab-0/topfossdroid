"use strict";

const PAGE_SIZE=50;
const DEFAULT_SORT="popular";
const SORTS=["popular","downloads","stars","trending","fresh","gems","updated","added"];
const state={apps:[],filtered:[],visible:PAGE_SIZE,sort:DEFAULT_SORT,q:"",categories:new Set(),catalogs:new Set(),hosts:new Set(),updated:"",minStars:"",minDownloads:"",activeOnly:false,multiCatalog:false,app:"",sourceNames:new Map(),details:new Map(),hasTrendData:false};
let detailHistoryPushed=false;
const $=id=>document.getElementById(id);
const numberFormat=new Intl.NumberFormat(undefined,{notation:"compact",maximumFractionDigits:1});
const iconObserver="IntersectionObserver" in window?new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;const image=entry.target;image.src=image.dataset.src;image.removeAttribute("data-src");iconObserver.unobserve(image)}},{rootMargin:"200px"}):null;

function escapeHtml(value){return String(value??"").replace(/[&<>'"]/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char])}
function compact(value){return value==null?"—":numberFormat.format(value).toLowerCase()}
function signedCompact(value){return value==null?"—":`${value>0?"+":""}${compact(value)}`}
function relative(value){if(!value)return"—";const days=Math.floor((Date.now()-new Date(value))/86400000);if(days<1)return"today";if(days<30)return`${days}d ago`;if(days<365)return`${Math.floor(days/30)}mo ago`;return`${Math.floor(days/365)}y ago`}
function dateCell(value){return value?`<span title="${escapeHtml(String(value).slice(0,10))}">${relative(value)}</span>`:"—"}
function repoUrl(app){const domains={github:"github.com",gitlab:"gitlab.com",codeberg:"codeberg.org"};return app.repoPath&&domains[app.host]?`https://${domains[app.host]}/${app.repoPath}`:null}
function safePackage(packageId){return packageId.replace(/[^A-Za-z0-9._-]/g,"_")}

function normalize(raw){
  const app={package:raw.p,name:raw.n,summary:raw.s||"",icon:raw.i,categories:raw.c||[],sources:raw.src||[],repoPath:raw.r,host:raw.h,stars:raw.st??null,stars30d:raw.t30??null,trendPeriodDays:raw.tp??null,downloads:raw.dl??null,popular:raw.po??null,updatedAt:raw.u,addedAt:raw.a,eligible:raw.e!==false};
  app._search=[app.name,app.package,app.summary,app.repoPath,app.categories.join(" ")].filter(Boolean).join(" ").toLowerCase();
  app._updatedTime=app.updatedAt?Date.parse(app.updatedAt):null;
  app._addedTime=app.addedAt?Date.parse(app.addedAt):null;
  return app;
}

function icon(app,size=48){
  const letter=app.name.slice(0,1).toUpperCase();
  if(!app.icon)return`<span class="fallback" aria-hidden="true">${escapeHtml(letter)}</span>`;
  const path=escapeHtml(app.icon);
  return`<img class="app-icon" width="${size}" height="${size}" loading="lazy" decoding="async" ${iconObserver?`data-src="${path}"`:`src="${path}"`} alt="" data-fallback="${escapeHtml(letter)}">`;
}

function observeIcons(root){root.querySelectorAll("img[data-src]").forEach(image=>iconObserver.observe(image))}
function commaSet(value){return new Set((value||"").split(",").filter(Boolean))}
function readUrl(){
  const params=new URLSearchParams(location.search);
  for(const key of ["q","updated","sort","app"])state[key]=params.get(key)||"";
  state.categories=commaSet(params.get("category"));
  state.catalogs=commaSet(params.get("catalog")||params.get("source"));
  state.hosts=commaSet(params.get("host"));
  state.minStars=params.get("stars")||"";
  state.minDownloads=params.get("downloads")||"";
  state.activeOnly=params.get("active")==="1"||params.get("status")==="active";
  state.multiCatalog=params.get("multi")==="1";
  const oldStatus=params.get("status");
  if(!state.hosts.size&&["github","gitlab","codeberg"].includes(oldStatus))state.hosts.add(oldStatus);
  if(!SORTS.includes(state.sort))state.sort=DEFAULT_SORT;
}
function pageUrl(){
  const params=new URLSearchParams();
  for(const key of ["q","updated","app"])if(state[key])params.set(key,state[key]);
  for(const [key,values] of [["category",state.categories],["catalog",state.catalogs],["host",state.hosts]])if(values.size)params.set(key,[...values].join(","));
  if(state.sort!==DEFAULT_SORT)params.set("sort",state.sort);
  if(state.minStars)params.set("stars",state.minStars);
  if(state.minDownloads)params.set("downloads",state.minDownloads);
  if(state.activeOnly)params.set("active","1");
  if(state.multiCatalog)params.set("multi","1");
  return`${location.pathname}${params.size?`?${params}`:""}`;
}
function writeUrl(){history.replaceState(null,"",pageUrl())}
function names(a,b){return a.name.localeCompare(b.name)||a.package.localeCompare(b.package)}
function nullableDesc(a,b){if(a==null&&b==null)return 0;if(a==null)return 1;if(b==null)return-1;return b-a}
function compare(a,b){
  if(state.sort==="trending")return state.hasTrendData?(nullableDesc(a.stars30d,b.stars30d)||nullableDesc(a.stars,b.stars)||names(a,b)):(nullableDesc(a.stars,b.stars)||names(a,b));
  if(state.sort==="gems")return nullableDesc(a.downloads,b.downloads)||(a.stars-b.stars)||nullableDesc(a._updatedTime,b._updatedTime)||names(a,b);
  const key={popular:"popular",fresh:"popular",stars:"stars",downloads:"downloads",updated:"_updatedTime",added:"_addedTime"}[state.sort];
  return nullableDesc(a[key],b[key])||names(a,b);
}

function rankingEligible(app){
  if(!app.eligible)return false;
  const days=app._updatedTime===null?null:(Date.now()-app._updatedTime)/86400000;
  if(state.sort==="fresh")return app.popular!==null&&days!==null&&days<=180&&(app.stars>=500||app.downloads>=10000);
  if(state.sort==="gems")return days!==null&&days<=365&&app.stars!==null&&app.stars<5000&&app.downloads!==null&&app.downloads>=25000;
  if(state.sort==="popular")return app.popular!==null;
  return true;
}

function matches(app,ignore=""){
  const query=state.q.trim().toLowerCase();
  const updatedAfter={"30d":30,"3m":90,"6m":183,"1y":365}[state.updated];
  const cutoff=updatedAfter?Date.now()-updatedAfter*86400000:null;
  const activeCutoff=Date.now()-365*86400000;
  return rankingEligible(app)
    &&(!query||app._search.includes(query))
    &&(ignore==="categories"||!state.categories.size||app.categories.some(value=>state.categories.has(value)))
    &&(ignore==="catalogs"||!state.catalogs.size||app.sources.some(value=>state.catalogs.has(value)))
    &&(!cutoff||(app._updatedTime!==null&&app._updatedTime>=cutoff))
    &&(!state.minStars||(app.stars!==null&&app.stars>=Number(state.minStars)))
    &&(!state.minDownloads||(app.downloads!==null&&app.downloads>=Number(state.minDownloads)))
    &&(ignore==="hosts"||!state.hosts.size||state.hosts.has(app.host))
    &&(!state.activeOnly||(app._updatedTime!==null&&app._updatedTime>=activeCutoff))
    &&(!state.multiCatalog||app.sources.length>=2);
}
function apply(){
  state.filtered=state.apps.filter(app=>matches(app)).sort(compare);
  state.visible=Math.min(PAGE_SIZE,state.filtered.length);
  renderFacets();
  renderChips();
  render(true);
  writeUrl();
}

const sortCopy={
  popular:"Popular now, based on stars, F-Droid downloads, and recent activity.",
  downloads:"Ranked by available F-Droid download metrics.",
  stars:"Ranked by public GitHub, GitLab, and Codeberg stars.",
  trending:"Ranked by stars gained over the last 30 days.",
  fresh:"Popular apps with recent project activity.",
  gems:"Strong F-Droid usage without huge repository audiences.",
  updated:"Apps with the most recent available activity.",
  added:"Apps most recently added to the index."
};
const sortLabels={popular:"Most Popular",downloads:"Most Downloaded",stars:"Most Starred",trending:"Trending",fresh:"Fresh & Popular",gems:"Hidden Gems",updated:"Recently Updated",added:"Recently Added"};

function row(app,index){
  const trend=app.stars30d==null?"—":signedCompact(app.stars30d);
  const dateValue=state.sort==="added"?app.addedAt:app.updatedAt;
  const dateLabel=state.sort==="added"?"added":"updated";
  const shownSources=app.sources.slice(0,2);
  const catalogs=shownSources.map(source=>`<span class="badge">${escapeHtml(state.sourceNames.get(source)||source)}</span>`).join("")+(app.sources.length>2?`<span class="badge">+${app.sources.length-2}</span>`:"");
  const starsMissing=app.stars==null?' missing" title="Star data unavailable':"";
  const trendMissing=app.stars30d==null?' missing" title="Trend data unavailable':"";
  const downloadsMissing=app.downloads==null?' missing" title="F-Droid download data unavailable':"";
  const dateMissing=dateValue==null?` missing" title="${dateLabel[0].toUpperCase()+dateLabel.slice(1)} date unavailable`:"";
  return`<article class="app-row sort-${state.sort}"><span class="rank" aria-label="Rank ${index+1}">${index+1}</span><div class="app-cell">${icon(app)}<div class="app-copy"><button class="app-button" data-package="${escapeHtml(app.package)}">${escapeHtml(app.name)}</button><div class="summary">${escapeHtml(app.summary||"No summary available")}</div><div class="repo-line">${escapeHtml(app.repoPath||app.package)}${catalogs?`<span class="badges">${catalogs}</span>`:""}</div></div></div><span class="metric stars-metric${starsMissing}">${compact(app.stars)}<small>stars</small></span><span class="metric trend-metric${trendMissing}">${trend}<small>30d trend</small></span><span class="metric downloads-metric${downloadsMissing}">${compact(app.downloads)}<small>downloads</small></span><span class="metric date-metric${dateMissing}">${dateCell(dateValue)}<small>${dateLabel}</small></span></article>`;
}

function updateResultChrome(){
  const currentCount=state.apps.filter(app=>app.eligible).length;
  $("count").textContent=state.filtered.length===currentCount?`${currentCount.toLocaleString()} apps`:`${state.filtered.length.toLocaleString()} of ${currentCount.toLocaleString()} apps`;
  $("load-more").hidden=state.visible>=state.filtered.length;
  document.querySelectorAll("[data-sort]").forEach(button=>{const active=button.dataset.sort===state.sort;button.classList.toggle("active",active);button.setAttribute("aria-pressed",active)});
  $("sort-select").value=state.sort;
  $("sort-description").textContent=sortCopy[state.sort];
  $("date-heading").textContent=state.sort==="added"?"Added":"Updated";
  $("context-heading").textContent=state.categories.size===1?`${sortLabels[state.sort]} in ${[...state.categories][0]}`:"";
  const filtering=Boolean(state.categories.size||state.catalogs.size||state.updated||state.minStars||state.minDownloads||state.hosts.size||state.activeOnly||state.multiCatalog);
  $("clear-filters").hidden=!filtering;
  $("trending-definition").hidden=state.sort!=="trending"||!state.hasTrendData;
  $("trend-fallback").hidden=state.sort!=="trending"||state.hasTrendData;
}

function facetOptions(id,values,selected,group){
  const counts=new Map(values.map(value=>[value.id,0]));
  for(const app of state.apps){if(!matches(app,group))continue;const appValues=group==="categories"?app.categories:group==="catalogs"?app.sources:[app.host];for(const value of new Set(appValues))if(counts.has(value))counts.set(value,counts.get(value)+1)}
  $(id).innerHTML=values.map(value=>`<label data-facet-label="${escapeHtml(value.name.toLowerCase())}"><input type="checkbox" data-group="${group}" value="${escapeHtml(value.id)}" ${selected.has(value.id)?"checked":""}><span>${escapeHtml(value.name)}</span><small>${counts.get(value.id).toLocaleString()}</small></label>`).join("");
}
function renderFacets(){
  facetOptions("category-options",state.categoryValues,state.categories,"categories");
  facetOptions("catalog-options",state.catalogValues,state.catalogs,"catalogs");
  const hosts=[{id:"github",name:"GitHub"},{id:"gitlab",name:"GitLab"},{id:"codeberg",name:"Codeberg"}].filter(value=>state.apps.some(app=>app.host===value.id));
  facetOptions("host-options",hosts,state.hosts,"hosts");
  const labels={"30d":"30d","3m":"3mo","6m":"6mo","1y":"1yr"};
  $("categories-button").textContent=`Categories${state.categories.size?` ${state.categories.size}`:""}`;
  $("catalogs-button").textContent=`Catalogs${state.catalogs.size?` ${state.catalogs.size}`:""}`;
  $("updated").options[0].textContent=state.updated?`Updated: ${labels[state.updated]}`:"Updated";
  const more=state.hosts.size+Boolean(state.minStars)+Boolean(state.minDownloads)+Boolean(state.activeOnly);
  $("more-button").textContent=`More filters${more?` ${more}`:""}`;
  const categoryQuery=$("category-search").value.trim().toLowerCase();
  document.querySelectorAll("#category-options label").forEach(label=>label.hidden=!label.dataset.facetLabel.includes(categoryQuery));
  const catalogQuery=$("catalog-search").value.trim().toLowerCase();
  document.querySelectorAll("#catalog-options label").forEach(label=>label.hidden=!label.dataset.facetLabel.includes(catalogQuery));
}
function renderChips(){
  const chips=[];const add=(group,value,label)=>chips.push(`<button type="button" data-remove-group="${group}" data-remove-value="${escapeHtml(value)}">${escapeHtml(label)} <span aria-hidden="true">×</span></button>`);
  for(const value of state.categories)add("categories",value,value);
  for(const value of state.catalogs)add("catalogs",value,state.sourceNames.get(value)||value);
  for(const value of state.hosts)add("hosts",value,{github:"GitHub",gitlab:"GitLab",codeberg:"Codeberg"}[value]||value);
  const updated={"30d":"Updated < 30 days","3m":"Updated < 3 months","6m":"Updated < 6 months","1y":"Updated < 1 year"};if(state.updated)add("updated",state.updated,updated[state.updated]);
  if(state.minStars)add("minStars",state.minStars,`Stars ${compact(Number(state.minStars))}+`);
  if(state.minDownloads)add("minDownloads",state.minDownloads,`Downloads ${compact(Number(state.minDownloads))}+`);
  if(state.activeOnly)add("activeOnly","1","Active only");
  if(state.multiCatalog)add("multiCatalog","1","Multi-catalog");
  $("filter-chips").innerHTML=chips.join("");
  document.querySelectorAll("[data-preset]").forEach(button=>{const active={active:state.activeOnly,stars:state.minStars==="1000",downloads:state.minDownloads==="100000",multi:state.multiCatalog}[button.dataset.preset];button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active))});
}

function render(reset){
  const results=$("results");
  if(reset)results.innerHTML="";
  const start=reset?0:results.querySelectorAll(".app-row").length;
  const apps=state.filtered.slice(start,state.visible);
  if(reset&&!apps.length)results.innerHTML='<div class="message"><strong>No apps match your search and filters.</strong><br>Try broader criteria or clear the filters.</div>';
  else results.insertAdjacentHTML("beforeend",apps.map((app,index)=>row(app,start+index)).join(""));
  observeIcons(results);
  updateResultChrome();
}

function fact(label,value){return value!==null&&value!==undefined&&value!==""?`<div class="fact"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`:""}
function link(label,url,primary=false){return url?`<a ${primary?'class="primary" ':""}href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`:""}
function detailMetrics(app){const catalogs=app.sources.map(source=>state.sourceNames.get(source)||source).join(" · ");return`<div class="detail-metrics"><div class="detail-metric"><strong>★ ${compact(app.stars)}</strong><span>stars</span></div><div class="detail-metric"><strong>${compact(app.downloads)}</strong><span>F-Droid downloads</span></div><div class="detail-metric"><strong>${relative(app.updatedAt)}</strong><span>updated</span></div><div class="detail-metric"><strong>${escapeHtml(catalogs||"—")}</strong><span>catalogs</span></div></div>`}
function popularReason(app){const signals=[];if(app.stars!==null)signals.push("repository stars");if(app.downloads!==null)signals.push("F-Droid downloads");if(app.updatedAt)signals.push("activity");return`Strong ${signals.join(signals.length>2?", ":" and ").replace(/, ([^,]+)$/,", and $1")}.`}
function rankContext(app){
  const index=state.filtered.indexOf(app);
  if(index<0)return"";
  const rank=index+1;
  const label={popular:"Popular",downloads:"Most Downloaded",stars:"Most Starred",trending:"Trending",fresh:"Fresh & Popular",gems:"Hidden Gem",updated:"Recently Updated",added:"Recently Added"}[state.sort];
  const reason={popular:popularReason(app),downloads:`${compact(app.downloads)} measured F-Droid downloads.`,stars:`${compact(app.stars)} ${{github:"GitHub",gitlab:"GitLab",codeberg:"Codeberg"}[app.host]||"repository"} stars.`,trending:app.stars30d==null?"Ranked by total stars while trend history is collected.":`${signedCompact(app.stars30d)} stars over the last ${app.trendPeriodDays||30} days.`,fresh:"Popular and updated recently.",gems:"Strong F-Droid usage without a massive repository audience.",updated:`Updated ${relative(app.updatedAt)}.`,added:`Added ${relative(app.addedAt)}.`}[state.sort];
  return`<div class="rank-context"><strong>#${rank} ${escapeHtml(label)}</strong><span>${escapeHtml(reason)}</span></div>`;
}
function detailBase(app){return`<article class="detail"><div class="detail-head">${icon(app,68)}<div><h2 id="detail-title">${escapeHtml(app.name)}</h2><p class="summary">${escapeHtml(app.summary)}</p></div></div>${rankContext(app)}${detailMetrics(app)}<div class="detail-actions"><button id="copy-app-link" type="button">Copy link</button><span id="app-copy-status" class="copy-status" role="status"></span></div><div id="extended-detail" class="detail-description" aria-live="polite">Loading app details…</div></article>`}
function extendedDetail(app,detail){
  const repo=detail.repo||{};
  const catalog=(detail.sources||[]).find(source=>source.url);
  const primaryUrl=catalog?.url||detail.sourceCode||repoUrl(app)||detail.website;
  const authority={official_ecosystem:"stable ecosystem catalog",official_project:"official project repo",curated_third_party:"curated catalog",discovery_only:"discovery source"};
  const provenance=(detail.sources||[]).map(source=>`${state.sourceNames.get(source.id)||source.name||source.id} — ${source.channel==="stable"?(authority[source.authority]||"stable source"):source.channel}`).join("; ");
  return`${primaryUrl?`<div class="primary-action">${link(catalog?`View on ${state.sourceNames.get(catalog.id)||catalog.name||catalog.id}`:"View source code",primaryUrl,true)}</div>`:""}<div class="detail-description">${escapeHtml(detail.description||"No detailed description is available.")}</div><h3 class="detail-section-title">Project details</h3><dl class="facts">${fact("Categories",app.categories.join(", "))}${fact("Sources",provenance)}${fact("License",detail.license)}${fact("Version",detail.latestVersion)}${fact("Repository activity",(repo.pushedAt||repo.updatedAt||app.updatedAt||"").slice(0,10))}${fact("Latest release",repo.latestReleaseTag)}${fact("Forks",repo.forks?.toLocaleString())}${fact("Open issues",repo.openIssues?.toLocaleString())}${fact("Anti-features",(detail.antiFeatures||[]).join(", "))}${fact("Package",app.package)}</dl><div class="links">${link("Source code",detail.sourceCode||repoUrl(app))}${link("Website",detail.website)}${link("Issue tracker",detail.issueTracker)}${link("Donate",detail.donate)}${(detail.sources||[]).map(source=>link(state.sourceNames.get(source.id)||source.name||source.id,source.url)).join("")}</div>`;
}

async function showDetails(packageId,updateHistory=true){
  const app=state.apps.find(item=>item.package===packageId);
  if(!app){state.app="";writeUrl();return}
  state.app=packageId;
  if(updateHistory){history.pushState(null,"",pageUrl());detailHistoryPushed=true}
  $("detail-content").innerHTML=detailBase(app);
  observeIcons($("detail-content"));
  if(!$("details").open)$("details").showModal();
  let request=state.details.get(packageId);
  if(!request){request=fetch(`data/details/${encodeURIComponent(safePackage(packageId))}.json`).then(response=>{if(!response.ok)throw new Error(`Details request failed (${response.status})`);return response.json()});state.details.set(packageId,request)}
  try{
    const detail=await request;
    if($("details").open&&$("detail-title")?.textContent===app.name)$("extended-detail").outerHTML=extendedDetail(app,detail);
  }catch(error){state.details.delete(packageId);if($("details").open&&$("detail-title")?.textContent===app.name)$("extended-detail").textContent="Extended details are currently unavailable."}
}

async function init(){
  readUrl();
  try{
    const [appsResponse,metaResponse]=await Promise.all([fetch("data/apps.json"),fetch("data/meta.json")]);
    if(!appsResponse.ok)throw new Error(`Catalog request failed (${appsResponse.status})`);
    const meta=metaResponse.ok?await metaResponse.json():null;
    for(const source of meta?.sources||[])state.sourceNames.set(source.id,source.name);
    state.apps=(await appsResponse.json()).map(normalize);
    state.hasTrendData=state.apps.some(app=>app.stars30d!==null&&app.stars30d!==0);
    state.catalogValues=(meta?.sources||[...new Set(state.apps.flatMap(app=>app.sources))].map(id=>({id,name:id}))).sort((a,b)=>a.name.localeCompare(b.name));
    state.categoryValues=[...new Set(state.apps.flatMap(app=>app.categories))].sort().map(value=>({id:value,name:value}));
    const controls={q:"search",updated:"updated",minStars:"min-stars",minDownloads:"min-downloads"};
    for(const [key,id] of Object.entries(controls)){const element=$(id);element.value=state[key];element.addEventListener(key==="q"?"input":"change",()=>{state[key]=element.value;apply()})}
    $("clear-search").hidden=!state.q;
    const activeOnly=$("active-only");activeOnly.checked=state.activeOnly;activeOnly.addEventListener("change",()=>{state.activeOnly=activeOnly.checked;apply()});
    $("total-apps").textContent=`${(meta?.rankingEligibleApps??state.apps.filter(app=>app.eligible).length).toLocaleString()} apps`;
    if(meta)$("generated").textContent=`Updated ${new Date(meta.generatedAt).toLocaleDateString(undefined,{month:"short",day:"numeric"})}`;
    apply();
    if(state.app)showDetails(state.app,false);
  }catch(error){$("results").innerHTML=`<div class="message"><strong>Rankings unavailable</strong><br>${escapeHtml(error.message)}. Please try again later.</div>`;$("count").textContent=""}
}

$("results").addEventListener("click",event=>{const button=event.target.closest(".app-button");if(button)showDetails(button.dataset.package)});
document.addEventListener("error",event=>{const image=event.target;if(!(image instanceof HTMLImageElement)||!image.dataset.fallback)return;const fallback=document.createElement("span");fallback.className="fallback";fallback.setAttribute("aria-hidden","true");fallback.textContent=image.dataset.fallback;iconObserver?.unobserve(image);image.replaceWith(fallback)},true);
document.querySelectorAll("[data-sort]").forEach(button=>button.addEventListener("click",()=>{state.sort=button.dataset.sort;apply()}));
$("sort-select").addEventListener("change",event=>{state.sort=event.target.value;apply()});
$("search").addEventListener("input",()=>{$("clear-search").hidden=!$("search").value});
$("clear-search").addEventListener("click",()=>{$("search").value="";state.q="";$("clear-search").hidden=true;apply();$("search").focus()});
function closePopovers(except){document.querySelectorAll(".filter-panel").forEach(panel=>{if(panel===except)return;panel.hidden=true;document.querySelector(`[aria-controls="${panel.id}"]`)?.setAttribute("aria-expanded","false")})}
document.querySelectorAll(".filter-button").forEach(button=>button.addEventListener("click",()=>{const panel=$(button.getAttribute("aria-controls"));const opening=panel.hidden;closePopovers(panel);panel.hidden=!opening;button.setAttribute("aria-expanded",String(opening));if(opening)(panel.querySelector('input[type="search"]')||panel.querySelector("input,select,button"))?.focus()}));
document.addEventListener("click",event=>{if(!event.target.closest(".popover"))closePopovers()});
document.addEventListener("keydown",event=>{if(event.key==="Escape"){const open=document.querySelector(".filter-panel:not([hidden])");if(open){const button=document.querySelector(`[aria-controls="${open.id}"]`);closePopovers();button?.focus()}}});
document.querySelector(".filter-row").addEventListener("change",event=>{const input=event.target.closest("input[data-group]");if(!input)return;const values=state[input.dataset.group];input.checked?values.add(input.value):values.delete(input.value);apply()});
document.querySelectorAll("[data-clear-group]").forEach(button=>button.addEventListener("click",()=>{state[button.dataset.clearGroup].clear();apply()}));
$("category-search").addEventListener("input",event=>{const query=event.target.value.trim().toLowerCase();document.querySelectorAll("#category-options label").forEach(label=>label.hidden=!label.dataset.facetLabel.includes(query))});
$("catalog-search").addEventListener("input",event=>{const query=event.target.value.trim().toLowerCase();document.querySelectorAll("#catalog-options label").forEach(label=>label.hidden=!label.dataset.facetLabel.includes(query))});
$("filter-chips").addEventListener("click",event=>{const button=event.target.closest("[data-remove-group]");if(!button)return;const group=button.dataset.removeGroup;if(state[group] instanceof Set)state[group].delete(button.dataset.removeValue);else if(group==="activeOnly"||group==="multiCatalog")state[group]=false;else state[group]="";syncControls();apply()});
function syncControls(){for(const [key,id] of [["updated","updated"],["minStars","min-stars"],["minDownloads","min-downloads"]])$(id).value=state[key];$("active-only").checked=state.activeOnly}
document.querySelector(".quick-filters").addEventListener("click",event=>{const button=event.target.closest("[data-preset]");if(!button)return;const preset=button.dataset.preset;if(preset==="active")state.activeOnly=!state.activeOnly;if(preset==="stars")state.minStars=state.minStars==="1000"?"":"1000";if(preset==="downloads")state.minDownloads=state.minDownloads==="100000"?"":"100000";if(preset==="multi")state.multiCatalog=!state.multiCatalog;syncControls();apply()});
$("clear-filters").addEventListener("click",()=>{state.categories.clear();state.catalogs.clear();state.hosts.clear();state.updated=state.minStars=state.minDownloads="";state.activeOnly=state.multiCatalog=false;syncControls();apply()});
$("load-more").addEventListener("click",()=>{state.visible=Math.min(state.visible+PAGE_SIZE,state.filtered.length);render(false)});
async function copyText(value,status){
  try{
    if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(value);
    else{const input=document.createElement("textarea");input.value=value;input.setAttribute("readonly","");input.style.position="fixed";input.style.opacity="0";document.body.append(input);input.select();if(!document.execCommand("copy"))throw new Error("Copy failed");input.remove()}
    status.textContent="Link copied";
  }catch(error){status.textContent="Could not copy link"}
  setTimeout(()=>{status.textContent=""},2200);
}
$("share-ranking").addEventListener("click",()=>copyText(location.href,$("share-status")));
$("details").addEventListener("click",event=>{if(event.target.id==="copy-app-link")copyText(location.href,$("app-copy-status"))});
function closeDetails(){
  if(!$("details").open)return;
  if(detailHistoryPushed){history.back();return}
  state.app="";history.replaceState(null,"",pageUrl());$("details").close();
}
$("details").querySelector(".close").addEventListener("click",closeDetails);
$("details").addEventListener("click",event=>{if(event.target===$("details"))closeDetails()});
$("details").addEventListener("cancel",event=>{event.preventDefault();closeDetails()});
addEventListener("popstate",()=>{detailHistoryPushed=false;readUrl();syncControls();apply();if(state.app)showDetails(state.app,false);else if($("details").open)$("details").close()});
init();

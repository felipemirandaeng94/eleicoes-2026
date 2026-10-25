// Gera assets/mapa-ufs.json a partir do GeoJSON das 27 UFs (Code for America,
// mesma fonte usada antes pelo painel). Executar fora do site:
//   npm i d3-geo@3 polylabel@2 mapshaper@0.6 && node tools/gerar_mapa.mjs brazil-states.geojson
// Saída: caminhos SVG já projetados (cônica equivalente de Albers ajustada ao
// Brasil), posição de rótulos, rótulos externos com linha-guia para UFs pequenas,
// enquadramento das cinco regiões e um recorte ampliado do Distrito Federal.
import fs from 'node:fs';
import {geoConicEqualArea, geoPath, geoBounds} from 'd3-geo';
import polylabel from 'polylabel';
import mapshaper from 'mapshaper';

const [, , entrada, saida = 'assets/mapa-ufs.json'] = process.argv;

// O d3-geo trabalha na esfera e exige anel externo em sentido horário (furos em
// anti-horário); GeoJSON RFC 7946 usa o oposto. Sem isso, cada UF vira "o mundo
// menos a UF".
function areaPlana(anel) {
  let s = 0;
  for (let i = 0; i < anel.length - 1; i++) s += anel[i][0] * anel[i + 1][1] - anel[i + 1][0] * anel[i][1];
  return s / 2;
}
function rebobinar(fc) {
  const ajustar = (poly) => poly.map((anel, i) => {
    const horario = areaPlana(anel) < 0;
    return (i === 0) === horario ? anel : [...anel].reverse();
  });
  for (const f of fc.features) {
    const g = f.geometry;
    g.coordinates = g.type === 'Polygon' ? ajustar(g.coordinates) : g.coordinates.map(ajustar);
  }
  return fc;
}
const bruto = rebobinar(JSON.parse(fs.readFileSync(entrada, 'utf8')));

const REGIOES = {
  'Norte': ['AC', 'AM', 'AP', 'PA', 'RO', 'RR', 'TO'],
  'Nordeste': ['AL', 'BA', 'CE', 'MA', 'PB', 'PE', 'PI', 'RN', 'SE'],
  'Centro-Oeste': ['DF', 'GO', 'MS', 'MT'],
  'Sudeste': ['ES', 'MG', 'RJ', 'SP'],
  'Sul': ['PR', 'RS', 'SC'],
};
const regiaoDe = Object.fromEntries(Object.entries(REGIOES).flatMap(([r, l]) => l.map(u => [u, r])));

// Simplificação topológica: fronteiras compartilhadas continuam coincidentes.
const out = await mapshaper.applyCommands(
  '-i in.json -simplify visvalingam weighted 7% keep-shapes -o out.json format=geojson precision=0.0001',
  {'in.json': JSON.stringify(bruto)});
const simples = rebobinar(JSON.parse(out['out.json'].toString()));

const W = 1000, M = 18, MARGEM_LESTE = 150;
const proj = geoConicEqualArea().parallels([-2, -22]).rotate([54, 0]).center([0, -15])
  .fitWidth(W - MARGEM_LESTE - M, simples);
const [[bx0, by0], [, by1]] = geoPath(proj).bounds(simples);
proj.translate([proj.translate()[0] + M - bx0, proj.translate()[1] + M - by0]);
const H = Math.ceil(by1 - by0 + 2 * M);
const path = geoPath(proj).digits(1);

const r1 = v => Math.round(v * 10) / 10;
function maiorAnel(geom) {
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
  let melhor = null, area = -1;
  for (const p of polys) {
    const proj0 = p.map(anel => anel.map(c => proj(c)));
    const a = Math.abs(proj0[0].reduce((s, c, i, arr) => {
      const n = arr[(i + 1) % arr.length]; return s + c[0] * n[1] - n[0] * c[1];
    }, 0) / 2);
    if (a > area) { area = a; melhor = proj0; }
  }
  return {aneis: melhor, area};
}

// Rótulos externos (UFs pequenas ou estreitas no litoral e o DF), em unidades do
// viewBox. Coluna à direita do litoral nordestino com espaçamento >= 36 unidades
// para continuar legível em celulares (~11 px de texto em 340 px de largura).
const ufs = [];
for (const f of simples.features) {
  const uf = f.properties.sigla, nome = f.properties.name;
  const {aneis, area} = maiorAnel(f.geometry);
  const lab = polylabel(aneis, 0.5);
  const b = path.bounds(f);
  ufs.push({
    uf, nome, regiao: regiaoDe[uf], ibge: f.properties.codigo_ibg,
    d: path(f), rotulo: [r1(lab[0]), r1(lab[1])], area: Math.round(area),
    bbox: b.flat().map(r1),
  });
}
const porUf = Object.fromEntries(ufs.map(u => [u.uf, u]));

// Coluna de rótulos externos: x fixo a leste do ponto mais oriental do Brasil.
const xLeste = Math.max(...ufs.map(u => u.bbox[2]));
const colX = r1(xLeste + 44);
// 48 unidades ≈ 16 px num mapa de ~335 px de largura (celular): rótulos de 14 px não se tocam.
const nordeste = ['RN', 'PB', 'PE', 'AL', 'SE'];
const yIni = porUf.RN.rotulo[1] - 52;
nordeste.forEach((uf, i) => {
  porUf[uf].externo = {texto: [colX, r1(yIni + i * 48)], ancora: porUf[uf].rotulo};
});
for (const [uf, dy] of [['ES', -6], ['RJ', 26]]) {
  const u = porUf[uf];
  porUf[uf].externo = {texto: [r1(u.bbox[2] + 46), r1(u.rotulo[1] + dy)], ancora: u.rotulo};
}
// DF: marcador na posição real e rótulo externo com linha-guia para nordeste,
// em área de baixa densidade de rótulos (entre GO, MG e BA).
const df = porUf.DF;
const cdf = [r1((df.bbox[0] + df.bbox[2]) / 2), r1((df.bbox[1] + df.bbox[3]) / 2)];
df.rotulo = cdf;
df.externo = {texto: [r1(cdf[0] + 58), r1(cdf[1] - 46)], ancora: cdf, marcador: true};

// Enquadramento das regiões (união dos bboxes, com folga para rótulos externos).
const regioes = {};
for (const [nome, lista] of Object.entries(REGIOES)) {
  const bb = lista.map(u => porUf[u].bbox);
  let x0 = Math.min(...bb.map(b => b[0])), y0 = Math.min(...bb.map(b => b[1]));
  let x1 = Math.max(...bb.map(b => b[2])), y1 = Math.max(...bb.map(b => b[3]));
  for (const u of lista) if (porUf[u].externo) x1 = Math.max(x1, porUf[u].externo.texto[0] + 30);
  const pad = Math.max(x1 - x0, y1 - y0) * 0.06;
  regioes[nome] = {ufs: lista, bbox: [x0 - pad, y0 - pad, x1 + pad, y1 + pad].map(r1)};
}

// Recorte ampliado do DF: projeção própria, geometria original (sem simplificar),
// mostrando o DF e o entorno (GO e MG) recortados pelo retângulo do inset.
const brutoPorUf = Object.fromEntries(bruto.features.map(f => [f.properties.sigla, f]));
const [[lo0, la0], [lo1, la1]] = geoBounds(brutoPorUf.DF);
const folga = 0.35;
// Anel em sentido horário (convenção do d3); no sentido inverso o d3 enquadraria o globo inteiro.
const caixa = {type: 'Feature', geometry: {type: 'Polygon', coordinates: [[
  [lo0 - folga, la0 - folga], [lo0 - folga, la1 + folga], [lo1 + folga, la1 + folga],
  [lo1 + folga, la0 - folga], [lo0 - folga, la0 - folga]]]}};
const IW = 220, IH = 160;
const pi = geoConicEqualArea().parallels([-2, -22]).rotate([54, 0]).center([0, -15])
  .fitExtent([[6, 6], [IW - 6, IH - 6]], caixa);
pi.clipExtent([[0, 0], [IW, IH]]);
const pathI = geoPath(pi).digits(1);
const inset = {
  viewBox: [0, 0, IW, IH],
  df: pathI(brutoPorUf.DF),
  entorno: ['GO', 'MG'].map(u => ({uf: u, d: pathI(brutoPorUf[u])})),
  rotulo: pi([(lo0 + lo1) / 2, (la0 + la1) / 2]).map(r1),
};

const resultado = {
  fonte: 'Code for America click_that_hood brazil-states.geojson (limites estaduais IBGE), simplificado 7% com topologia preservada',
  projecao: 'Cônica equivalente de Albers, paralelos -2 e -22, meridiano central -54',
  viewBox: [0, 0, W, H], regioes, ufs: ufs.sort((a, b) => a.uf.localeCompare(b.uf)), inset,
};
fs.mkdirSync(saida.split('/').slice(0, -1).join('/') || '.', {recursive: true});
fs.writeFileSync(saida, JSON.stringify(resultado));
console.log(`${saida}: ${(fs.statSync(saida).size / 1024).toFixed(1)} KB, ${ufs.length} UFs`);

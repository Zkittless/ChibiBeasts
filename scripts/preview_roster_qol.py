"""Export the actual roster, gear, Care and exploration UI for offline review.

All game state belongs to a disposable example database. The exported controls
navigate saved real payloads locally; they never call Discord or spend live items.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import adventure_party, db as storage, gear_art, journal_state, owned_roster
from utils.gear_view import GearView
from utils.journal import JournalView
from utils.roster_view import OwnedRosterView


STYLE = """
html{color-scheme:dark}body{margin:0;padding:24px 12px;background:#202126;color:#dbdee1;font:14px/1.5 Arial,sans-serif}
#cb-roster-qol{max-width:920px;margin:auto}#cb-roster-qol *{box-sizing:border-box}
#cb-roster-qol header,#cb-roster-qol .cq-tools,#cb-roster-qol .cq-note{max-width:640px;margin:0 auto}
#cb-roster-qol h1{font-size:25px;line-height:1.25;font-weight:500;margin:0 0 8px}
#cb-roster-qol header p{color:#b5bac1;margin:0 0 18px}
#cb-roster-qol .cq-tools{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
#cb-roster-qol .cq-tools label{flex:0 0 auto}#cb-roster-qol .cq-tools select{flex:1;min-width:130px}
#cb-roster-qol .cq-stage{max-width:640px;margin:auto;padding:0 0 16px}
#cb-roster-qol .cq-message{padding:14px 14px 12px;background:#2b2d31;border:1px solid #4e5058;border-left:4px solid #b7ad87;border-radius:7px;color:#dbdee1;overflow-wrap:anywhere}
#cb-roster-qol .cq-message h2{font-size:18px;line-height:1.4;font-weight:500;margin:0 0 8px}
#cb-roster-qol h3{font-size:18px;line-height:1.4;font-weight:500;margin:12px 0 6px}
#cb-roster-qol .cq-description{margin:0 0 12px}#cb-roster-qol strong{font-weight:600}
#cb-roster-qol .cq-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 20px;margin:12px 0}
#cb-roster-qol .cq-field{grid-column:1/-1;min-width:0}#cb-roster-qol .cq-field[data-inline="true"]{grid-column:auto}
#cb-roster-qol .cq-field-name{font-weight:600;margin-bottom:3px}#cb-roster-qol .cq-footer{font-size:12px;color:#b5bac1;margin-top:14px}
#cb-roster-qol .cq-art{max-width:100%;display:block;max-height:340px;object-fit:contain;object-position:left;image-rendering:pixelated;margin:12px 0}
#cb-roster-qol .cq-gallery .cq-art{width:100%;max-height:none;object-fit:contain;margin:4px 0 12px}
#cb-roster-qol .cq-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:10px;min-width:0}
#cb-roster-qol button{font:inherit;padding:8px 12px;max-width:100%;border:0;border-radius:4px;background:#41434a;color:#f2f3f5}
#cb-roster-qol button[data-style="1"]{background:#5865f2}#cb-roster-qol button[data-style="3"]{background:#278747}
#cb-roster-qol button:disabled{opacity:.5}#cb-roster-qol select,#cb-roster-qol input{font:inherit;color:#dbdee1;background:#1e1f22;border:1px solid #4e5058;border-radius:4px;padding:10px 8px;width:100%;min-width:0;max-width:100%}
#cb-roster-qol .cq-row select{flex:1 1 100%}#cb-roster-qol code{font-size:.94em;padding:1px 3px;background:#1e1f22;border-radius:3px}
#cb-roster-qol .cq-text{margin:12px 0}#cb-roster-qol .cq-text h3{margin-top:0}
#cb-roster-qol .cq-section{display:grid;grid-template-columns:1fr 88px;gap:12px;align-items:start;margin:12px 0}
#cb-roster-qol .cq-section .cq-art{max-height:88px;width:88px;margin:0}#cb-roster-qol .cq-section .cq-text{margin:0}
#cb-roster-qol .cq-note{padding:0 2px;color:#b5bac1;min-height:44px;font-size:12px}
#cb-roster-qol .cq-search{max-width:640px;margin:0 auto 12px;padding:12px;background:#2b2d31;border:1px solid #4e5058;border-radius:6px}
#cb-roster-qol .cq-search label{display:block;margin-bottom:7px}#cb-roster-qol .cq-search .cq-row{margin-top:8px}
@media(max-width:420px){body{padding:16px 8px}#cb-roster-qol h1{font-size:22px}#cb-roster-qol .cq-message{padding:12px 10px}#cb-roster-qol .cq-fields{gap:12px}#cb-roster-qol .cq-field[data-inline="true"]{grid-column:1/-1}#cb-roster-qol button{padding:8px}#cb-roster-qol .cq-section{grid-template-columns:1fr 72px;gap:8px}#cb-roster-qol .cq-section .cq-art{width:72px}}
@media(pointer:coarse){#cb-roster-qol button,#cb-roster-qol select,#cb-roster-qol input{min-height:44px}#cb-roster-qol input,#cb-roster-qol select{font-size:16px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""


SCRIPT = r"""
(() => {
  const root=document.getElementById('cb-roster-qol'), data=__DATA__, art=__ART__;
  const stage=root.querySelector('.cq-stage'), picker=root.querySelector('#cq-screen'), note=root.querySelector('.cq-note'), searchForm=root.querySelector('.cq-search');
  const names={roster:'Full roster', filtered:'Search by nickname', rarity:'Filter and sort', detail:'Individual beast', gear:'Choose a beast in /gear', explore:'Explore destination selection', saved:'Saved explore default', care:'Care · heal preview', healed:'Care · after one potion', blocked:'Care · healing blocked', permanent:'Care · permanent item review'};
  let current='roster', context='all-number', rosterMode='overview', rosterPage=0, selected=data.lead_id, gearQuery='', gearSelected=data.lead_id, realm='woods', saved='', carePage='care', careSelected=data.lead_id;
  const baseline='Actual Discord payloads · example collection · offline navigation only.';
  for(const [key,name] of Object.entries(names)){if(!data.screens[key])continue;const option=document.createElement('option');option.value=key;option.textContent=name;picker.append(option);}
  function html(value){const span=document.createElement('span');span.textContent=String(value??'');return span.innerHTML;}
  function markdown(value){return html(value).replace(/\\([_~*`])/g,'$1').split('\n').map(line=>line.startsWith('### ')?'<h3>'+line.slice(4)+'</h3>':line.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*([^*]+)\*/g,'<em>$1</em>')+'<br>').join('');}
  function image(url,description,parent){const img=document.createElement('img');img.className='cq-art';img.src=art[url.replace('attachment://','')]||url;img.alt=description||'Equipped companion';parent.append(img);}
  function actionFor(payload,id){if(payload.actions[id])return payload.actions[id];const parts=id.split(':').slice(2);if(/^\d+$/.test(parts[parts.length-1]))parts.pop();return parts.join(':');}
  function component(item,parent,payload){
    if(item.type===17){for(const child of item.components)component(child,parent,payload);return;}
    if(item.type===10){const text=document.createElement('div');text.className='cq-text';text.innerHTML=markdown(item.content);parent.append(text);return;}
    if(item.type===12){const gallery=document.createElement('div');gallery.className='cq-gallery';for(const entry of item.items)image(entry.media.url,entry.description,gallery);parent.append(gallery);return;}
    if(item.type===9){const section=document.createElement('div');section.className='cq-section';const text=document.createElement('div');for(const child of item.components)component(child,text,payload);section.append(text);if(item.accessory?.media)image(item.accessory.media.url,item.accessory.description,section);parent.append(section);return;}
    if(item.type===1){const row=document.createElement('div');row.className='cq-row';for(const child of item.components)component(child,row,payload);parent.append(row);return;}
    if(item.type===2){const button=document.createElement('button');button.type='button';button.textContent=item.label;button.dataset.style=item.style;button.disabled=!!item.disabled;button.onclick=()=>act(actionFor(payload,item.custom_id));parent.append(button);return;}
    if(item.type===3){const select=document.createElement('select');select.setAttribute('aria-label',item.placeholder||'Choose an option');select.disabled=!!item.disabled;const empty=document.createElement('option');empty.value='';empty.textContent=item.placeholder||'Choose';empty.disabled=true;select.append(empty);for(const entry of item.options){const option=document.createElement('option');option.value=entry.value;option.textContent=entry.label;option.selected=!!entry.default;select.append(option);}if(!item.options.some(entry=>entry.default))select.value='';select.onchange=()=>choose(actionFor(payload,item.custom_id),select.value);parent.append(select);}
  }
  function renderPayload(payload){const message=document.createElement('div');message.className='cq-message';if(payload.embed){const embed=payload.embed;const heading=document.createElement('h2');heading.innerHTML=markdown(embed.title||'').replace(/<br>$/,'');message.append(heading);if(embed.description){const description=document.createElement('div');description.className='cq-description';description.innerHTML=markdown(embed.description);message.append(description);}if(embed.fields?.length){const fields=document.createElement('div');fields.className='cq-fields';for(const item of embed.fields){const field=document.createElement('div');field.className='cq-field';field.dataset.inline=!!item.inline;field.innerHTML='<div class="cq-field-name">'+markdown(item.name)+'</div>'+markdown(item.value);fields.append(field);}message.append(fields);}if(embed.image?.url)image(embed.image.url,'Your companion wearing equipped armor',message);if(embed.footer?.text){const footer=document.createElement('div');footer.className='cq-footer';footer.textContent=embed.footer.text;message.append(footer);}}
    for(const item of payload.components)component(item,message,payload);stage.append(message);
  }
  function rosterPayload(){const group=data.roster[context];if(rosterMode==='detail'&&group.details[selected])return group.details[selected];rosterMode='overview';rosterPage=Math.min(Math.max(rosterPage,0),group.pages.length-1);return group.pages[rosterPage];}
  function gearPayload(){const group=data.gear[gearQuery]||data.gear[''];return group[gearSelected]||Object.values(group)[0];}
  function render(){stage.replaceChildren();searchForm.hidden=true;root.dataset.screen=current;let payload;if(['roster','filtered','detail','rarity'].includes(current))payload=rosterPayload();else if(current==='gear')payload=gearPayload();else if(current==='explore'||current==='saved')payload=data.explore[saved]?.[realm]||data.explore[''][realm];else payload=data.care_targets[careSelected]?.[carePage]||data.screens[carePage];renderPayload(payload);picker.value=current;}
  function show(screen){if(!names[screen]||!data.screens[screen])return;current=screen;note.textContent=baseline;if(screen==='roster'){context='all-number';rosterMode='overview';rosterPage=0;}if(screen==='filtered'){context='query:Ember';rosterMode='overview';rosterPage=0;}if(screen==='rarity'){context='rare-level';rosterMode='overview';rosterPage=0;}if(screen==='detail'){context='all-number';rosterMode='detail';selected=data.lead_id;}if(screen==='gear'){gearQuery='';gearSelected=data.lead_id;}if(screen==='explore'){realm='woods';saved='';}if(screen==='saved'){realm='trenches';saved='trenches';}if(['care','healed','blocked','permanent'].includes(screen)){carePage=screen;careSelected=screen==='blocked'?data.blocked_id:data.lead_id;}render();}
  function openCare(id){if(!data.care_targets[id])return;current='care';carePage='care';careSelected=id;note.textContent=baseline;render();}
  function openSearch(){searchForm.hidden=false;const input=searchForm.querySelector('input');input.value='';input.focus();note.textContent='Search the exported sample by any full nickname, species or exact #number.';}
  function act(action){
    if(action==='roster:search'||action==='gear:search')return openSearch();
    if(action==='roster:previous'){rosterPage--;return render();}if(action==='roster:next'){rosterPage++;return render();}
    if(action==='roster:back'){rosterMode='overview';return render();}
    if(action==='roster:prev-beast'||action==='roster:next-beast'){const ids=data.roster[context].ids;const index=ids.indexOf(selected);selected=ids[Math.min(ids.length-1,Math.max(0,index+(action==='roster:prev-beast'?-1:1)))];rosterPage=Math.floor(ids.indexOf(selected)/10);return render();}
    if(action==='roster:clear'){context='all-number';rosterPage=0;rosterMode='overview';return render();}
    if(action==='gear:clear'){gearQuery='';gearSelected=data.lead_id;return render();}
    if(action==='gear:previous'||action==='gear:next'){const ids=data.gear_ids[gearQuery]||data.gear_ids[''];const page=Math.floor(ids.indexOf(gearSelected)/25)+(action==='gear:previous'?-1:1);gearSelected=ids[Math.min(ids.length-1,Math.max(0,page*25))];return render();}
    if(action==='roster:care')return openCare(selected);if(action==='gear:care')return openCare(gearSelected);
    if(action==='care:apply'){if(carePage==='care'&&careSelected===data.lead_id){current='healed';carePage='healed';return render();}note.textContent='This export captures one real potion use in a disposable sample. Additional spending happens only in Discord.';return;}
    if(action==='care:review'){current='permanent';carePage='permanent';return render();}
    if(action==='care:confirm'){note.textContent='The real Discord confirmation spends one permanent item. This offline preview keeps the captured review state.';return;}
    if(action==='care:previous'||action==='care:next'){const page=Math.floor(data.care_ids.indexOf(careSelected)/25)+(action==='care:previous'?-1:1);return openCare(data.care_ids[Math.min(data.care_ids.length-1,Math.max(0,page*25))]);}
    if(action.startsWith('save_destination:')){saved=action.split(':')[1];current='saved';note.textContent='Saved sample preference. No charge spent; Begin remains a separate action.';return render();}
    if(action==='clear_destination'){saved='';current='explore';return render();}
    if(action==='begin'){note.textContent='In Discord, Begin explicitly spends one exploration charge and saves the expedition. This offline preview stops before that action.';return;}
    if(action.endsWith(':refresh')||action==='refresh')return render();
    note.textContent='This control uses your saved trainer in Discord. The offline sample only navigates exported states.';
  }
  function choose(action,value){
    if(action==='roster:beast'){selected=value;rosterMode='detail';current='detail';return render();}
    if(action==='roster:rarity'||action==='roster:sort'){let [rarity,sort]=context.startsWith('query:')?['all','number']:context.split('-');if(action==='roster:rarity')rarity=value;else sort=value;const key=rarity+'-'+sort;if(data.roster[key]){context=key;rosterMode='overview';rosterPage=0;return render();}}
    if(action==='gear:beast'){gearSelected=value;return render();}
    if(action==='realm'){realm=value;return render();}
    if(action==='care:item'&&value==='pixie_dust_potion')return openCare(careSelected);
    if(action==='care:item'&&value==='star_candy_shards'){current='permanent';carePage='permanent';return render();}
    if(action==='care:companion'&&data.care_targets[value])return openCare(value);
    render();
    note.textContent='The exported sample includes this control; its live action runs in Discord.';
  }
  searchForm.onsubmit=event=>{event.preventDefault();const value=searchForm.querySelector('input').value.trim();const normalized=value.toLowerCase();const query=data.queries.find(query=>query.toLowerCase()===normalized);if(query!==undefined){if(current==='gear'){gearQuery=query;gearSelected=Object.keys(data.gear[query])[0]||data.lead_id;}else{context='query:'+query;rosterMode='overview';rosterPage=0;}note.textContent=baseline;render();}else note.textContent='This offline export supports full sample names, species and #numbers. Live Discord search also accepts partial names.';};
  searchForm.querySelector('[data-cancel]').onclick=()=>searchForm.hidden=true;
  picker.onchange=()=>show(picker.value);root.qolPreview={show,getState:()=>({screen:current,context,rosterMode,rosterPage,selected,gearQuery,gearSelected,realm,saved,careSelected})};show('roster');root.dataset.ready='true';
})();
"""


def actions(view, attributes):
    return {getattr(view, attribute).custom_id: action for attribute, action in attributes.items()
            if getattr(view, attribute, None) is not None}


async def export(output, *, without_care=False):
    output.mkdir(parents=True, exist_ok=True)
    portraits = output / 'portraits'
    portraits.mkdir(exist_ok=True)
    art, checks = {}, []

    async def portrait(species, armor_id=''):
        filename = gear_art.portrait_filename(species, armor_id)
        if filename not in art:
            rendered = await gear_art.render_portrait(species, armor_id)
            if rendered is None:
                raise RuntimeError(f'Missing preview portrait: {species}/{armor_id}')
            (portraits / rendered.filename).write_bytes(rendered.data)
            art[rendered.filename] = 'portraits/' + rendered.filename
        return filename

    async def legacy(view, embed, name, action_map, appearance=None):
        if appearance:
            filename = await portrait(*appearance)
            embed.set_image(url='attachment://' + filename)
        payload = dict(embed=embed.to_dict(), components=view.to_components(), actions=action_map)
        assert len(embed) <= 6000 and len(view.children) <= 25
        assert all(len(child.options) <= 25 for child in view.children if hasattr(child, 'options'))
        checks.append(dict(page=name, characters=len(embed), components=len(view.children), kind='Embed+View'))
        return payload

    with tempfile.TemporaryDirectory(prefix='chibibeasts-roster-qol-') as directory, patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Roster preview')
        await storage.update_player(42, level=25, gold=8250)
        catalog = storage.load_beasts()
        species = ['twine', 'gloop', 'barkley', 'prismite']
        for rarity in ('common', 'uncommon', 'rare', 'epic', 'legendary'):
            species.extend(key for key, beast in catalog.items() if beast['rarity'] == rarity and key not in species and not beast.get('evolution_only'))
            # Keep several rarities visible in a fixed 30-companion sample.
            species = species[:4 + (('common', 'uncommon', 'rare', 'epic', 'legendary').index(rarity) + 1) * 5]
        for key in catalog:
            if len(species) >= 30:
                break
            if key not in species:
                species.append(key)
        ids = [await storage.add_beast_to_player(42, dict(catalog[key], level=7 + index % 12), starter=index == 0)
               for index, key in enumerate(species[:30])]
        await adventure_party.set_slot(42, ids[1], 1)
        await adventure_party.set_slot(42, ids[2], 2)
        async with storage.connect() as db:
            await db.execute("UPDATE player_beasts SET nickname='Ember',hp=MAX(1,max_hp-45),happiness=75 WHERE id=?", (ids[0],))
            await db.execute("UPDATE player_beasts SET nickname='Mistral' WHERE id=?", (ids[26],))
            await db.executemany('INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(42,?,?)',
                                 [('gnome_iron_plating', ids[0]), ('kelpie_scale_mail', ids[1]), ('sprite_silk_attire', ids[2])])
            await db.commit()
        for item_id, quantity in (('pixie_dust_potion', 4), ('phoenix_elixir', 2), ('star_candy_shards', 1), ('brambleberries', 3), ('gnome_grown_carrots', 3)):
            await storage.add_item(42, item_id, quantity)
        rows = await storage.get_player_beasts(42)
        armor = await gear_art.equipped_armor_ids(42)
        queries = ['', 'Ember', 'Twine', '#27']
        queries.extend(owned_roster.name(row) for row in rows)
        queries.extend((catalog[row['beast_id']]['name']) for row in rows)
        queries.extend('#' + str(owned_roster.number(row)) for row in rows)
        queries = list(dict.fromkeys(queries))
        roster = {}
        view = OwnedRosterView(42, 'Trailkeeper')
        roster_actions = actions(view, dict(rarity_select='roster:rarity', sort_select='roster:sort', beast_select='roster:beast',
            prev_page='roster:previous', next_page='roster:next', prev_btn='roster:prev-beast', next_btn='roster:next-beast',
            back_button='roster:back', search_button='roster:search', clear_button='roster:clear', refresh_button='roster:refresh', care_button='roster:care'))
        view.rows, view.player = rows, await storage.get_player(42)
        view.inventory = {item['item_id']: item['quantity'] for item in await storage.get_inventory(42)}
        contexts = [(rarity + '-' + sort, owned_roster.PickerState(rarity=rarity, sort=sort))
                    for rarity in ('all',) + owned_roster.RARITIES + ('special',)
                    for sort in ('number', 'level', 'rarity')]
        contexts.extend(('query:' + query, owned_roster.PickerState(query=query)) for query in queries)
        try:
            for key, state in contexts:
                view.picker = state
                matches = view.matches()
                group = dict(pages=[], details={}, ids=[str(row['id']) for row in matches])
                pages = max(1, (len(matches) + 9) // 10)
                for page in range(pages):
                    view.mode, view.picker.page, view.selected_id = 'overview', page, None
                    embed, _ = view.render()
                    group['pages'].append(await legacy(view, embed, 'roster:' + key + ':' + str(page), roster_actions))
                for index, row in enumerate(matches):
                    view.mode, view.picker.page, view.selected_id = 'detail', index // 10, row['id']
                    embed, _ = view.render()
                    group['details'][str(row['id'])] = await legacy(view, embed, 'detail:' + key + ':' + str(row['id']), roster_actions,
                        (row['beast_id'], armor.get(row['id'], '')))
                roster[key] = group
        finally:
            view.stop()

        gear, gear_ids = {}, {}
        view = GearView(42)
        gear_actions = actions(view, dict(select='gear:beast', previous='gear:previous', next='gear:next', search='gear:search',
                                         clear='gear:clear', refresh='gear:refresh', care='gear:care'))
        try:
            await view.reload()
            for query in queries:
                view.picker = owned_roster.PickerState(query=query)
                matches = owned_roster.filtered(view.rows, view.picker)
                gear_ids[query] = [str(row['id']) for row in matches]
                gear[query] = {}
                for index, row in enumerate(matches):
                    view.selected_id, view.picker.page = row['id'], index // 25
                    embed = view.render()
                    gear[query][str(row['id'])] = await legacy(view, embed, 'gear:' + query + ':' + str(row['id']), gear_actions,
                        (row['beast_id'], view.armor_and_rune()[0]))
        finally:
            view.stop()

        explore = {}
        for default in ('',) + tuple(key for key, realm in journal_state.BIOMES.items() if realm['min_level'] <= 25):
            if default:
                await journal_state.save_explore_default(42, default)
            else:
                await journal_state.clear_explore_default(42)
            state = await journal_state.snapshot(42)
            explore[default] = {}
            for realm in journal_state.BIOMES:
                view = JournalView(42, state, page='adventure', realm=realm)
                try:
                    view.render()
                    assert view.content_length() <= 4000 and view.total_children_count <= 40
                    for filename, appearance in view.portraits.items():
                        assert filename == await portrait(*appearance)
                    if view.header.name not in art:
                        (portraits / view.header.name).write_bytes(view.header.read_bytes())
                        art[view.header.name] = 'portraits/' + view.header.name
                    explore[default][realm] = dict(components=view.to_components(), actions={})
                    checks.append(dict(page=f'explore:{default}:{realm}', characters=view.content_length(),
                                       components=view.total_children_count, kind='LayoutView'))
                finally:
                    view.stop()
        screens = dict(roster=roster['all-number']['pages'][0], filtered=roster['query:Ember']['pages'][0],
                       rarity=roster['rare-level']['pages'][0], detail=roster['all-number']['details'][str(ids[0])],
                       gear=gear[''][str(ids[0])], explore=explore['']['woods'], saved=explore['trenches']['trenches'])
        care_proof, care_targets = None, {}
        if not without_care:
            care_targets, care_proof = await export_care(screens, checks, legacy, ids)
    data = dict(screens=screens, roster=roster, gear=gear, gear_ids=gear_ids, explore=explore,
                lead_id=str(ids[0]), blocked_id=str(ids[2]), queries=queries, care_proof=care_proof,
                care_targets=care_targets, care_ids=[str(row_id) for row_id in ids])
    (output / 'components.json').write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    script = SCRIPT.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/')).replace('__ART__', json.dumps(art))
    document = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>ChibiBeasts · Roster and Care</title><style>' + STYLE + '</style></head><body>'
                '<main id="cb-roster-qol"><header><h1>Your companions, within reach.</h1>'
                '<p>Search the full roster, choose gear targets, care for an individual and confirm your next expedition.</p></header>'
                '<div class="cq-tools"><label for="cq-screen">Preview</label><select id="cq-screen" aria-label="Preview screen"></select></div>'
                '<form class="cq-search" hidden><label for="cq-query">Nickname, species or collection number</label>'
                '<input id="cq-query" autocomplete="off" placeholder="Ember, Twine or #27"><div class="cq-row">'
                '<button type="submit" data-style="1">Search sample</button><button type="button" data-cancel>Cancel</button></div></form>'
                '<div class="cq-stage" aria-live="polite"></div><p class="cq-note" role="status"></p></main><script>' + script + '</script></body></html>')
    (output / 'roster-qol.html').write_text(document, encoding='utf-8')
    report = dict(status='passed', scope='Actual game Embed/View and LayoutView exports from a disposable 30-companion example database. Local controls navigate saved payloads; no live Discord actions.',
                  total_payloads=len(checks), companion_count=len(ids), artwork_count=len(art), screens=list(screens),
                  max_embed_characters=max(check['characters'] for check in checks if check['kind'] == 'Embed+View'),
                  max_layout_components=max(check['components'] for check in checks if check['kind'] == 'LayoutView'),
                  care_transaction=care_proof, checks=checks)
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'checks'}))
    return output / 'roster-qol.html'


async def export_care(screens, checks, legacy, ids):
    """Final Care UI hooks are intentionally imported only after export setup."""
    from utils import care
    from utils.care_view import CareView
    armor = await gear_art.equipped_armor_ids(42)

    def click():
        response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: True)
        return SimpleNamespace(user=SimpleNamespace(id=42), response=response, data={}, guild=None,
                               followup=SimpleNamespace(send=AsyncMock()),
                               edit_original_response=AsyncMock(return_value=SimpleNamespace(attachments=[])))

    async def save(view, screen, *, register=True):
        embed = view.render()
        mapping = actions(view, dict(companion_select='care:companion', item_select='care:item',
            use_button='care:review' if view.current_preview and view.current_preview.requires_confirmation else 'care:apply',
            confirm_button='care:confirm', previous_button='care:previous', next_button='care:next',
            search_button='care:search', refresh_button='care:refresh', clear_button='care:clear'))
        beast = view.state.beast
        payload = await legacy(view, embed, screen, mapping,
            (beast['beast_id'], armor.get(beast['id'], '')) if beast else None)
        if register:
            screens[screen] = payload
        return payload

    view = CareView(42, await care.snapshot(42, ids[0]))
    try:
        view.item_id = 'pixie_dust_potion'
        await save(view, 'care')
        before = dict(hp=view.state.beast['hp'], stock=view.state.inventory[view.item_id], active_id=ids[0])
        await view.handle(click(), 'use')
        await save(view, 'healed')
        after = dict(hp=view.state.beast['hp'], stock=view.state.inventory[view.item_id],
                     active_id=(await storage.get_active_beast(42))['id'])
        assert after['hp'] > before['hp'] and after['stock'] == before['stock'] - 1
        assert after['active_id'] == before['active_id']
        view.item_id = 'star_candy_shards'
        view.state = await care.snapshot(42, ids[0])
        view.render()
        quantity = view.state.inventory[view.item_id]
        await view.handle(click(), 'use')
        assert view.confirming and view.state.inventory[view.item_id] == quantity
        await save(view, 'permanent')
    finally:
        view.stop()
    async with storage.connect() as db:
        await db.execute('UPDATE player_beasts SET hp=0,knocked_out_until=? WHERE id=?',
                         ((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(), ids[2]))
        await db.commit()
    view = CareView(42, await care.snapshot(42, ids[2]))
    try:
        view.item_id = 'pixie_dust_potion'
        await save(view, 'blocked')
        assert not view.current_preview.usable and view.use_button.disabled
    finally:
        view.stop()
    targets = {str(ids[0]): {key: screens[key] for key in ('care', 'healed', 'permanent')},
               str(ids[2]): {'care': screens['blocked'], 'blocked': screens['blocked']}}
    rows = await storage.get_player_beasts(42)
    for index, row in enumerate(rows):
        if row['id'] == ids[0]:
            continue
        view = CareView(42, await care.snapshot(42, row['id']))
        try:
            view.picker.page = index // 25
            target = targets.setdefault(str(row['id']), {})
            if 'care' not in target:
                view.item_id = 'pixie_dust_potion'
                target['care'] = await save(view, f'care:target:{row["id"]}', register=False)
            view.item_id, view.confirming = 'star_candy_shards', True
            target['permanent'] = await save(view, f'care:review:{row["id"]}', register=False)
        finally:
            view.stop()
    proof = dict(scope='Real CareView Use callback and existing item transaction against the disposable preview database.',
                 before=before, after=after, permanent_review_consumed=0, ordinary_heal_ko_disabled=True)
    return targets, proof


async def capture(path, output):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000), device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#cb-roster-qol[data-ready="true"]')
        screens = await page.locator('#cq-screen option').evaluate_all('(nodes)=>nodes.map(node=>node.value)')
        for width, label in ((1100, 'desktop'), (390, 'mobile'), (320, 'narrow')):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in screens:
                await page.locator('#cq-screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(img=>img.complete&&img.naturalWidth>0)')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width, screen)
                await page.screenshot(path=str(output / f'{label}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, page=screen, no_overflow=True, all_art_loaded=True))
        await page.locator('#cq-screen').select_option('roster')
        await page.get_by_role('button', name='Next page', exact=True).click()
        assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().rosterPage') == 1
        menu = page.get_by_role('combobox', name='Open a companion’s details')
        chosen = await menu.locator('option[value]').evaluate_all('(nodes)=>nodes.find(node=>node.value).value')
        await menu.select_option(chosen)
        assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().selected') == chosen
        await page.get_by_role('button', name='Back to roster', exact=True).click()
        assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().rosterPage') == 1
        await page.get_by_role('button', name='Search', exact=True).click()
        await page.locator('#cq-query').fill('Ember')
        await page.get_by_role('button', name='Search sample', exact=True).click()
        assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().context') == 'query:Ember'
        await page.locator('#cq-screen').select_option('explore')
        await page.get_by_role('combobox', name='Choose your destination').select_option('trenches')
        await page.get_by_role('button', name='Save destination', exact=True).click()
        assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().saved') == 'trenches'
        if 'care' in screens:
            await page.locator('#cq-screen').select_option('detail')
            menu = page.get_by_role('combobox', name='Open a companion’s details')
            reserve = await menu.locator('option[value]').evaluate_all('(nodes)=>nodes.filter(node=>node.value)[1].value')
            await menu.select_option(reserve)
            await page.get_by_role('button', name='Care & items', exact=True).click()
            assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().careSelected') == reserve
            assert '#' + reserve in await page.locator('.cq-message h2').inner_text()
            await page.get_by_role('combobox', name='Choose an owned care item').select_option('star_candy_shards')
            assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().careSelected') == reserve
            await page.wait_for_function('Array.from(document.images).every(img=>img.complete&&img.naturalWidth>0)')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await page.screenshot(path=str(output / 'narrow-care-reserve.png'), full_page=True)
            await page.locator('#cq-screen').select_option('gear')
            await page.locator('.cq-stage select').first.select_option(reserve)
            await page.get_by_role('button', name='Care', exact=True).click()
            assert await page.evaluate('document.getElementById("cb-roster-qol").qolPreview.getState().careSelected') == reserve
            await page.wait_for_function('Array.from(document.images).every(img=>img.complete&&img.naturalWidth>0)')
        await browser.close()
    assert not errors, errors
    report = dict(status='passed', javascript_errors=errors, checks=checks, roster_pages=True,
                  direct_selection=True, back_preserves_page=True, search=True, saved_destination=True,
                  care_keeps_roster_and_gear_target='care' in screens)
    (output / 'browser-validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} viewport/page combinations; no overflow, missing artwork or JavaScript errors.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'roster-qol-preview')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    parser.add_argument('--without-care', action='store_true', help='Temporary export while the Care UI is being developed')
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path = await export(args.output, without_care=args.without_care)
    if args.capture:
        await capture(path, args.output)


if __name__ == '__main__':
    asyncio.run(main())

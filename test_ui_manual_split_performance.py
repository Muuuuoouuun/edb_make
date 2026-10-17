"""Bounded page work and keyboard safety in continuous crop editing."""
import unittest

from test_ui_home_flows import NODE_SETUP, run_node


SHORTCUT_SETUP = NODE_SETUP + r"""
const calls=[];
let handler;
const sandbox={
  useEffect: fn=>fn(),
  window:{addEventListener:(_name,fn)=>{handler=fn},removeEventListener(){}},
  manualSplit:{regions:[{}],selectedRegionIds:['r1']},manualSplitPage:{id:'page'},
  mutating:false,manualSplitShortcutHelpOpen:false,
  MANUAL_SPLIT_FAST_NUDGE_PX:10,MANUAL_SPLIT_NUDGE_PX:1,
  cancelManualSplitGesture:()=>calls.push('cancel'),
  setManualSplitMode:mode=>calls.push(mode),
  setManualSplitShortcutHelpOpen:value=>calls.push('help'),
  selectAllManualSplitRegions:()=>calls.push('all'),
  duplicateManualSplitSelected:()=>calls.push('duplicate'),
  toggleManualSplitPanelSide:()=>calls.push('panel'),
  deleteManualSplitSelected:()=>calls.push('delete'),
  applyManualPageSplit:()=>calls.push('apply'),
  nudgeManualSplitSelected:(x,y)=>calls.push([x,y]),
};
vm.createContext(sandbox);
vm.runInContext(section('function manualSplitShortcutKey(', 'function translateManualSplitBoxes(')
  + section('function isEditableKeyboardTarget(', 'function resetItemPlacement('),sandbox);
vm.runInContext(section('  useEffect(() => {\n    if (!manualSplit) return undefined;', '\n  if (!pages.length) {'),sandbox);
const fire=extra=>{
  const event={key:'',code:'',target:{tagName:'DIV'},preventDefault(){this.prevented=true},stopPropagation(){},...extra};
  handler(event);return event;
};
"""


class TestManualSplitPerformance(unittest.TestCase):
    def test_editing_only_renders_target_page_without_losing_source_index(self):
        run_node(NODE_SETUP + r"""
        const pages=Array.from({length:500},(_,index)=>({id:`p${index}`}));
        const reviewPageLookup=new Map(pages.map((page,index)=>[page.id,{page,index}]));
        const renderPages=manualSplit=>vm.runInNewContext(
          section('  const manualSplitPage =', '  const manualSplitScope =')+'\nrenderedReviewPages;',
          {pages,reviewPageLookup,manualSplit,useMemo:fn=>fn()});
        assert.equal(renderPages(null),pages);
        const selected=renderPages({pageId:'p499'});
        assert.equal(selected.length,1);assert.equal(selected[0],pages[499]);
        assert.equal(reviewPageLookup.get(selected[0].id).index,499);
        assert.equal(renderPages({pageId:'removed-page'}).length,0);
        """)

    def test_overlap_matches_pairwise_reference_for_random_boxes_and_edge_contacts(self):
        run_node(NODE_SETUP + r"""
        const sandbox={finiteNumber:(v,f)=>Number.isFinite(Number(v))?Number(v):f};
        vm.createContext(sandbox);
        vm.runInContext(section('function manualSplitBoxesOverlap(', 'function manualSplitShortcutKey('),sandbox);
        let seed=42;
        const rand=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/2**32};
        const brute=boxes=>boxes.some((a,i)=>boxes.slice(i+1).some(b=>sandbox.manualSplitBoxesOverlap(a,b)));
        for(let trial=0;trial<250;trial++) {
          const boxes=Array.from({length:30},()=>({bbox:{left:rand()*400,top:rand()*600,width:rand()*30,height:rand()*30}}));
          const snapshot=JSON.stringify(boxes);
          assert.equal(sandbox.manualSplitHasOverlaps(boxes),brute(boxes));
          assert.equal(JSON.stringify(boxes),snapshot);
        }
        const touching=[{bbox:{left:0,top:0,width:20,height:20}},{bbox:{left:20,top:0,width:20,height:20}}];
        assert.equal(sandbox.manualSplitHasOverlaps(touching),false);
        assert.equal(sandbox.manualSplitHasOverlaps([]),false);
        """)

    def test_shortcuts_support_korean_layout_but_ignore_composition_and_repeat(self):
        run_node(SHORTCUT_SETUP + r"""
        fire({key:'ㄴ',code:'KeyS'});assert.equal(calls.pop(),'stamp');
        fire({key:'ㄴ',code:'KeyS',isComposing:true});
        fire({key:'s',code:'KeyS',keyCode:229});
        fire({key:'d',ctrlKey:true,repeat:true});
        fire({key:'s',defaultPrevented:true});
        assert.equal(calls.length,0);
        fire({key:'d',ctrlKey:true,shiftKey:true});assert.equal(calls.length,0);
        fire({key:'d',metaKey:true});assert.equal(calls.pop(),'duplicate');
        """)

    def test_enter_preserves_buttons_inputs_and_modal_ownership(self):
        run_node(SHORTCUT_SETUP + r"""
        const button={tagName:'BUTTON',closest:selector=>selector.startsWith('button')?{}:null};
        assert.equal(fire({key:'Enter',target:button}).prevented,undefined);
        fire({key:'Enter',target:{tagName:'INPUT'}});
        fire({key:'Enter',target:{tagName:'DIV',closest:()=>({})}});
        assert.equal(calls.length,0);
        fire({key:'Enter',ctrlKey:true,target:button});assert.equal(calls.pop(),'apply');
        fire({key:'Enter'});assert.equal(calls.pop(),'apply');
        fire({key:'Enter',repeat:true});assert.equal(calls.length,0);
        """)

    def test_arrow_repeat_is_allowed_but_modified_navigation_does_not_move_boxes(self):
        run_node(SHORTCUT_SETUP + r"""
        fire({key:'ArrowRight',shiftKey:true,repeat:true});assert.deepEqual(calls.pop(),[10,0]);
        fire({key:'ArrowLeft',repeat:true});assert.deepEqual(calls.pop(),[-1,0]);
        fire({key:'ArrowLeft',ctrlKey:true});fire({key:'Delete',altKey:true});
        fire({key:'Backspace',metaKey:true});assert.equal(calls.length,0);
        let blurred=false;
        fire({key:'Escape',target:{tagName:'INPUT',blur(){blurred=true}}});
        assert.equal(blurred,true);assert.deepEqual(calls,['cancel','select']);
        """)

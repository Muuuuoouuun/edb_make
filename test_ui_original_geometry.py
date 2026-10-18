"""Original-page geometry must remain bounded and numeric edits must keep their intent."""
import unittest
from test_ui_home_flows import NODE_SETUP, run_node

SETUP = NODE_SETUP + r"""
const sandbox={finiteNumber:(v,f)=>Number.isFinite(Number(v))?Number(v):f};
vm.createContext(sandbox);
vm.runInContext(section('function adjustOriginalBox(', 'function OriginalNumberInput('),sandbox);
"""

class TestOriginalGeometry(unittest.TestCase):
    def test_position_changes_preserve_dimensions_at_edges(self):
        run_node(SETUP + r"""
        const box={left:100,top:100,width:320,height:240},page={width:1000,height:800};
        for(const left of [-1000,0,100,10000]) for(const top of [-1000,0,100,10000]){
          const next=sandbox.adjustOriginalBox(box,page,{left,top});
          assert.equal(next.width,320);assert.equal(next.height,240);
          assert(next.left>=0&&next.left+next.width<=1000);
          assert(next.top>=0&&next.top+next.height<=800);
        }
        assert.equal(JSON.stringify(box),JSON.stringify({left:100,top:100,width:320,height:240}));
        """)

    def test_margin_signs_presets_and_page_clamping(self):
        run_node(SETUP + r"""
        const box={left:100,top:100,width:800,height:400},page={width:1000,height:800};
        const margin=v=>({left:v,right:v,top:v,bottom:v});
        assert.equal(JSON.stringify(sandbox.originalMarginBox(box,page,margin(0))),JSON.stringify(box));
        const trim=sandbox.originalMarginBox(box,page,margin(-10));
        assert.equal(trim.left,180);assert.equal(trim.top,140);assert.equal(trim.width,640);assert.equal(trim.height,320);
        const surrounding=sandbox.originalMarginBox(box,page,margin(10));
        assert.equal(surrounding.left,20);assert.equal(surrounding.top,60);assert.equal(surrounding.width,960);assert.equal(surrounding.height,480);
        for(const a of [-100,-40,0,20,100]) for(const b of [-100,-40,0,20,100]){
          const next=sandbox.originalMarginBox(box,page,{left:a,top:b,right:b,bottom:a});
          assert(next.left>=0&&next.top>=0&&next.width>=1&&next.height>=1);
          assert(next.left+next.width<=1000&&next.top+next.height<=800);
        }
        """)

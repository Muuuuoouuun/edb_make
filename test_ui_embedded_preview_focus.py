"""Native PDF child-window blur must not dismiss active editor controls."""
import unittest
from test_ui_home_flows import NODE_SETUP, run_node

class TestEmbeddedPreviewFocus(unittest.TestCase):
    def test_child_window_focus_is_recovered_and_cleanup_restores_inert(self):
        run_node(NODE_SETUP + r"""
        const listeners=new Map(), docListeners=new Map();let cleanup,frame,focused=0,cancelled=0;
        const preview={inert:false,matches:s=>s==='.pending-preview-pdf'};
        const button={focus:()=>{focused++;sandbox.document.activeElement=button;}};
        const owner={contains:x=>x===button,querySelectorAll:()=>[button]};
        const sandbox={
          useEffect:effect=>{cleanup=effect();},
          document:{activeElement:button,querySelectorAll:()=>[preview],
            addEventListener:(type,fn)=>docListeners.set(type,fn),removeEventListener:type=>docListeners.delete(type)},
          window:{addEventListener:(type,fn)=>listeners.set(type,fn),removeEventListener:type=>listeners.delete(type),
            requestAnimationFrame:fn=>{frame=fn;return 1;},cancelAnimationFrame:()=>cancelled++},
        };
        vm.createContext(sandbox);
        vm.runInContext(section('function isEmbeddedPreviewFocus(', 'function ToolbarActionMenu('),sandbox);
        sandbox.usePreviewFocusIsolation(true,{current:owner});assert.equal(preview.inert,true);
        // Native objects report window blur, without a document focusin.
        sandbox.document.activeElement=preview;listeners.get('blur')();assert.equal(focused,0);
        frame();assert.equal(focused,1);assert.equal(sandbox.document.activeElement,button);
        sandbox.document.activeElement=preview;listeners.get('focus')();assert.equal(focused,2);
        sandbox.document.activeElement=button;listeners.get('blur')();cleanup();
        assert.equal(preview.inert,false);assert.equal(listeners.size,0);assert.equal(docListeners.size,0);assert.equal(cancelled,1);
        """)

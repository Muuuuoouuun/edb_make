"""Large selection rendering preserves visible geometry and current event state."""
import unittest

from test_ui_sidebar_hierarchy import NODE_SETUP, RENDER_SETUP, run_node


class TestSelectionPerformance(unittest.TestCase):
    def test_board_window_preserves_indices_active_item_and_dragged_item(self):
        run_node(NODE_SETUP + r"""
        const items=Array.from({length:500},(_,i)=>({id:`p${i}`}));
        const positions=items.map((it,i)=>({top:i*100,renderedHeightPages:.8,rowHeightPages:1,yRatio:0}));
        const sandbox={layout:{items,positions},boardViewport:{top:0,height:600},pageH:100,
          activeId:'p499',positioningId:'p10',useMemo:fn=>fn()};
        vm.createContext(sandbox);
        const render=()=>vm.runInContext('(()=>{'
          +section('  const renderedBoardItems = useMemo(', '  const previewEstimate =')
          +'return renderedBoardItems;})()',sandbox);
        let visible=render();
        assert(visible.length<20);
        assert.equal(visible.at(-1).i,499);
        assert.equal(visible.at(-1).it,items[499]);
        sandbox.boardViewport={top:20000,height:600};visible=render();
        assert(visible.length<25);
        assert(visible.some(row=>row.i===200));
        assert(visible.some(row=>row.i===10),'dragged tile remains mounted outside the viewport');
        assert(visible.some(row=>row.i===499),'active tile remains mounted for external navigation');
        assert(!visible.some(row=>row.i===100));
        assert.equal(sandbox.layout.items,items);
        assert.equal(positions[499].top,49900,'windowing cannot modify export geometry');
        """)

    def test_memoized_board_events_use_latest_handlers(self):
        run_node(RENDER_SETUP + r"""
        const sandbox=renderSandbox(),calls=[];
        Object.assign(sandbox,{MemoBoardTileContents:()=>null,hasPageChromeArtifactFlag:()=>false,
          problemDisplayName:it=>it.name,ALTERNATE_MODIFIER_LABEL:'Alt'});
        sandbox.React.memo=fn=>fn;
        compile(sandbox,section('function BoardTile(', 'function BoardTileContents('));
        const handlersRef={current:{onTileClick:()=>calls.push('old'),beginPositionDrag:()=>calls.push('old')}};
        const tree=sandbox.BoardTile({it:{id:'a',name:'A',step:'raw'},p:{top:0,height:80,
          snappedNext:1.2,renderedHeightPages:.8,xRatio:0,yRatio:0},i:0,contentW:800,pageH:100,
          isSelected:true,isActive:true,tileRefs:{current:{}},handlersRef});
        handlersRef.current={onTileClick:id=>calls.push(['select',id]),
          beginPositionDrag:(_e,it)=>calls.push(['drag',it.id])};
        tree.props.onClick({});tree.props.onPointerDown({});
        assert.deepEqual(calls,[['select','a'],['drag','a']]);
        assert.equal(tree.props['aria-pressed'],'true');
        """)

    def test_unselected_memoized_row_uses_current_bulk_delete_selection(self):
        run_node(RENDER_SETUP + r"""
        const sandbox=renderSandbox();let deletes=0;
        Object.assign(sandbox,{MemoRailItemDetails:()=>null,hasPageChromeArtifactFlag:()=>false,
          problemDisplayName:it=>it.name,PRIMARY_MODIFIER_NAME:'Control',ALTERNATE_MODIFIER_NAME:'Alt'});
        sandbox.React.memo=fn=>fn;
        compile(sandbox,section('function RailRow(', '// ─── CENTER:'));
        const handlersRef={current:{selectedItemCount:0,applyRailSelectionKeyboard:()=>false,
          removeSelectedItems:()=>deletes++}};
        const tree=sandbox.RailRow({it:{id:'a',name:'A'},i:0,isSelected:false,
          selectedItemCount:0,displayedCount:2,itemCount:2,itemRefs:{current:{}},
          suppressClickRef:{current:false},handlersRef});
        const row=walk(tree).find(node=>node.props['data-item-id']==='a');
        const event={key:'Delete',preventDefault(){},target:1,currentTarget:1};
        handlersRef.current.selectedItemCount=3;
        row.props.onKeyDown(event);assert.equal(deletes,1);
        handlersRef.current.selectedItemCount=0;
        row.props.onKeyDown(event);assert.equal(deletes,1);
        """)

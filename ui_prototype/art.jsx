// Placeholder SVG artwork for sample items. Two modes:
//  - "raw":   black ink on white paper (scanned textbook look)
//  - "chalk": chalk strokes on transparent (so the board color shows through)

const ART_KINDS = {
  'geometry-circle': function CircleArt({mode}){
    const ink = mode === 'chalk' ? '#f4ede0' : '#1a1a1a';
    const accent = mode === 'chalk' ? '#9ec9f0' : '#2f6fed';
    const fill = mode === 'chalk' ? 'rgba(244,237,224,.06)' : '#f4f5f7';
    const sw = mode === 'chalk' ? 2.2 : 1.6;
    const filter = mode === 'chalk' ? 'url(#chalkRough)' : undefined;
    return (
      <svg viewBox="0 0 200 150" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <defs>
          <filter id="chalkRough" x="-5%" y="-5%" width="110%" height="110%">
            <feTurbulence type="fractalNoise" baseFrequency="1.4" numOctaves="2" seed="3" />
            <feDisplacementMap in="SourceGraphic" scale="0.8" />
          </filter>
        </defs>
        <g filter={filter}>
          <circle cx="100" cy="80" r="42" fill={fill} stroke={ink} strokeWidth={sw} />
          <line x1="100" y1="80" x2="142" y2="80" stroke={ink} strokeWidth={sw}/>
          <line x1="42" y1="120" x2="170" y2="40" stroke={accent} strokeWidth={sw}/>
          <circle cx="100" cy="80" r="2" fill={ink} />
          <circle cx="142" cy="80" r="2" fill={ink} />
          <text x="96" y="76" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>O</text>
          <text x="145" y="76" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>P</text>
          <text x="118" y="92" fontSize="8" fontFamily="serif" fontStyle="italic" fill={ink}>r</text>
        </g>
      </svg>
    );
  },

  'equation': function EqArt({mode}){
    const ink = mode === 'chalk' ? '#f4ede0' : '#1a1a1a';
    const accent = mode === 'chalk' ? '#f6d365' : '#d54848';
    return (
      <svg viewBox="0 0 200 150" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <g fontFamily="'Times New Roman', serif" fill={ink}>
          <text x="100" y="48" fontSize="22" fontStyle="italic" textAnchor="middle">ax² + bx + c = 0</text>
          <line x1="40" y1="68" x2="160" y2="68" stroke={ink} strokeWidth="1"/>
          <text x="100" y="108" fontSize="26" fontStyle="italic" textAnchor="middle">
            x = <tspan fontSize="14">−b ± √(b²−4ac)</tspan>
          </text>
          <line x1="56" y1="118" x2="144" y2="118" stroke={ink} strokeWidth="1.4"/>
          <text x="100" y="134" fontSize="14" fontStyle="italic" textAnchor="middle">2a</text>
          <text x="14" y="42" fontSize="11" fill={accent} fontWeight="600">①</text>
          <text x="14" y="106" fontSize="11" fill={accent} fontWeight="600">②</text>
        </g>
      </svg>
    );
  },

  'table': function TableArt({mode}){
    const ink = mode === 'chalk' ? '#f4ede0' : '#1a1a1a';
    const hdr = mode === 'chalk' ? '#f5a7b8' : '#2f6fed';
    const cells = [
      ['θ','sin','cos','tan'],
      ['30°','½','√3/2','√3/3'],
      ['45°','√2/2','√2/2','1'],
      ['60°','√3/2','½','√3'],
    ];
    return (
      <svg viewBox="0 0 200 150" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <g fontFamily="'Times New Roman', serif" fill={ink}>
          {cells.map((row, ri) => row.map((c, ci) => {
            const x = 24 + ci * 40, y = 32 + ri * 26;
            const isHdr = ri === 0;
            return (
              <g key={`${ri}-${ci}`}>
                <rect x={x-18} y={y-14} width="36" height="22" fill="none"
                  stroke={ink} strokeWidth=".8" opacity=".6" />
                <text x={x} y={y} fontSize="10" textAnchor="middle"
                  fill={isHdr ? hdr : ink} fontStyle={isHdr ? 'normal' : 'italic'}
                  fontWeight={isHdr ? 600 : 400}>{c}</text>
              </g>
            );
          }))}
        </g>
      </svg>
    );
  },

  'graph': function GraphArt({mode}){
    const ink = mode === 'chalk' ? '#f4ede0' : '#1a1a1a';
    const curve = mode === 'chalk' ? '#9ec9f0' : '#2f6fed';
    const curve2 = mode === 'chalk' ? '#f5a7b8' : '#d54848';
    const grid = mode === 'chalk' ? 'rgba(244,237,224,.15)' : '#e6e8ec';
    return (
      <svg viewBox="0 0 200 150" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        {/* grid */}
        {[...Array(9)].map((_,i)=>(
          <line key={'v'+i} x1={20+i*20} y1="20" x2={20+i*20} y2="130" stroke={grid} strokeWidth=".5"/>
        ))}
        {[...Array(6)].map((_,i)=>(
          <line key={'h'+i} x1="20" y1={20+i*22} x2="180" y2={20+i*22} stroke={grid} strokeWidth=".5"/>
        ))}
        {/* axes */}
        <line x1="20" y1="75" x2="184" y2="75" stroke={ink} strokeWidth="1.2" markerEnd="url(#arr)" />
        <line x1="100" y1="135" x2="100" y2="16" stroke={ink} strokeWidth="1.2" markerEnd="url(#arr)" />
        <defs>
          <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" fill={ink}/>
          </marker>
        </defs>
        {/* parabola */}
        <path d="M40,30 Q100,160 160,30" fill="none" stroke={curve} strokeWidth="2"/>
        {/* line */}
        <path d="M30,120 L170,40" fill="none" stroke={curve2} strokeWidth="1.6" strokeDasharray="3 3"/>
        <text x="170" y="36" fontSize="9" fontFamily="serif" fontStyle="italic" fill={curve2}>y=x</text>
        <text x="40" y="40" fontSize="9" fontFamily="serif" fontStyle="italic" fill={curve}>y=x²</text>
      </svg>
    );
  },

  'geometry-triangles': function TriArt({mode}){
    const ink = mode === 'chalk' ? '#f4ede0' : '#1a1a1a';
    const fill1 = mode === 'chalk' ? 'rgba(158,201,240,.18)' : 'rgba(47,111,237,.08)';
    const fill2 = mode === 'chalk' ? 'rgba(245,167,184,.18)' : 'rgba(213,72,72,.08)';
    return (
      <svg viewBox="0 0 200 150" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <polygon points="30,120 90,120 60,50" fill={fill1} stroke={ink} strokeWidth="1.6"/>
        <polygon points="110,130 188,130 149,40" fill={fill2} stroke={ink} strokeWidth="1.6"/>
        <text x="25" y="135" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>A</text>
        <text x="92" y="135" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>B</text>
        <text x="56" y="46" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>C</text>
        <text x="105" y="142" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>A'</text>
        <text x="186" y="142" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>B'</text>
        <text x="143" y="36" fontSize="9" fontFamily="serif" fontStyle="italic" fill={ink}>C'</text>
        <path d="M95 90 Q105 80 110 90" fill="none" stroke={ink} strokeWidth=".8" strokeDasharray="2 2"/>
        <text x="98" y="78" fontSize="7" fill={ink} opacity=".7">∽</text>
      </svg>
    );
  },

  'paragraph': function ParaArt({mode}){
    const ink = mode === 'chalk' ? '#f4ede0' : '#1a1a1a';
    const accent = mode === 'chalk' ? '#f6d365' : '#aa6516';
    return (
      <svg viewBox="0 0 200 150" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <g fontFamily="serif" fill={ink}>
          <text x="18" y="32" fontSize="11" fontWeight="700">[24] 다음 글의 요지로 가장 적절한 것은?</text>
          {[
            "The most successful learners in any field share",
            "one habit: they review what they did wrong,",
            "not what they did right. This deliberate",
            "self-correction (오답 노트) creates a feedback",
            "loop that compounds over time, turning",
            "ordinary effort into measurable progress.",
          ].map((line, i) => (
            <text key={i} x="18" y={52 + i * 13} fontSize="9.5">
              {line}
            </text>
          ))}
          <line x1="18" y1="65" x2="60" y2="65" stroke={accent} strokeWidth="1.4" opacity=".7"/>
          <line x1="18" y1="104" x2="92" y2="104" stroke={accent} strokeWidth="1.4" opacity=".7"/>
        </g>
      </svg>
    );
  },
};

function ItemArt({kind, mode}){
  const C = ART_KINDS[kind] || ART_KINDS['equation'];
  return <C mode={mode} />;
}

window.ItemArt = ItemArt;
window.ART_KINDS = ART_KINDS;

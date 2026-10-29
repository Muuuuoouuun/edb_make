#!/usr/bin/env python3
"""Independent source-GT/actual-export observer. Never consumes passageQuality.

Original PDF pixels are registered independently into actual cutouts, then checked
against decoded final EDB images. Unproved registration yields null pixel scores.
Actual UI runtime evidence is supplied separately; file presence does not imply success.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspect_edb import parse_edb, extract_images
from PIL import Image, ImageDraw, ImageChops, ImageStat
import fitz
from scripts.glyph_shape_quality import glyph_shape_evidence
from scripts.foreground_material_quality import has_rgb_paper_and_ink


def fraction(results):
    """No empty-inventory perfect scores."""
    results = list(results)
    if not results:
        return None
    return {'numerator': sum(bool(ok) for _, ok in results), 'denominator': len(results),
            'failed_ids': [key for key, ok in results if not ok]}


def rect(box):
    if isinstance(box, dict):
        return (box['left'], box['top'], box['left'] + box['width'], box['top'] + box['height'])
    return tuple(box)


def intersect(a, b):
    return max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))


def coverage(a, b):
    return intersect(a, b) / max(1e-12, (a[2]-a[0])*(a[3]-a[1]))


def pdf_box(box, page):
    """PDF points to normalized, margin-cropped page pixels."""
    meta = page['metadata']; scale = meta['dpi']/72
    crop = meta.get('margin_crop_box') or {'left': 0, 'top': 0}
    return tuple(v*scale - crop['left' if i % 2 == 0 else 'top'] for i,v in enumerate(box))


def union_area(boxes):
    xs=sorted({x for b in boxes for x in (b[0],b[2])})
    total=0
    for left,right in zip(xs,xs[1:]):
        intervals=sorted((b[1],b[3]) for b in boxes if b[0]<right and b[2]>left)
        height=0; end=-math.inf
        for top,bottom in intervals:
            height += max(0,bottom-max(top,end)); end=max(end,bottom)
        total+=(right-left)*height
    return total


def region_iou(expected, actual):
    intersection=[(max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3]))
                  for a in expected for b in actual if intersect(a,b)>0]
    both=union_area(intersection); total=union_area(expected)+union_area(actual)-both
    return both/total if total else None


def final_foreground(rgba):
    """Transparent text uses alpha; opaque filled figures use visible RGB ink.

    A large two-dimensional alpha component filling >=85% of its box represents
    an opaque image surface. White fill is paper, not retained ink. The minimum
    20px extent in BOTH directions excludes rules and frame strokes; 1000px area
    excludes single glyphs. These are fixed pre-candidate measurement tolerances.
    """
    import cv2
    import numpy as np
    visible=rgba[...,3]>16
    n,labels,stats,_=cv2.connectedComponentsWithStats(visible.astype(np.uint8),8)
    surfaces=[]
    for component in range(1,n):
        x,y,w,h,area=map(int,stats[component])
        component_mask=labels[y:y+h,x:x+w]==component
        opaque_fraction=float((rgba[y:y+h,x:x+w,3][component_mask]>=245).mean())
        if w>=20 and h>=20 and area>=1000 and area/(w*h)>=.85 and opaque_fraction>=.85 and has_rgb_paper_and_ink(rgba[y:y+h,x:x+w],component_mask):
            local=visible[y:y+h,x:x+w]
            local[component_mask]=np.any(rgba[y:y+h,x:x+w,:3]<200,axis=2)[component_mask]
            surfaces.append([x,y,w,h,area])
    return visible,surfaces


def reading_order_valid(positions):
    return all(a[1]<b[1] for a,b in zip(positions,positions[1:]))


def assign_detections(units, expected_keys, iou_threshold=.8):
    """One-to-one assignment by frozen range and union-region IoU."""
    selected={}; results={unit['id']:False for unit in units}
    for unit in sorted(units,key=lambda u:(-u['source_region_iou'],u['id'])):
        key=tuple(unit['range'])
        if key in expected_keys and key not in selected and unit['source_region_iou']>=iou_threshold:
            selected[key]=unit['id'];results[unit['id']]=True
    return results,selected


def geometric_unit_order(units, box_key):
    """Order physical lines by baseline bands then x; same-baseline labels tie.

    Fragment reading order remains source-owned. Within a fragment the source GT
    explicitly does not prescribe ancillary same-baseline label linearization.
    """
    pending=sorted(units,key=lambda u:((u[box_key][1]+u[box_key][3])/2,u[box_key][0],u['id']))
    bands=[]
    for unit in pending:
        b=unit[box_key];cy=(b[1]+b[3])/2;h=b[3]-b[1]
        if bands and abs(cy-bands[-1]['cy'])<=.45*min(h,bands[-1]['height']):
            bands[-1]['units'].append(unit)
        else:bands.append({'cy':cy,'height':h,'units':[unit]})
    return [u['id'] for band in bands for u in sorted(band['units'],key=lambda u:(u[box_key][0],u['id']))]


def observe_required_lines(fragment, page, original, visible, dx, dy, reference_visible=None, final_strength=None, reference_strength=None):
    """Find each source-line identity independently in actual final foreground.

    Search final Y across the whole exported group, not at declared source Y.
    NCC on one-pixel Gaussian masks tolerates raster interpolation. Exact repeated
    text ties choose the nearest predicted position deterministically; their
    distinct observed occurrences are counted against the source inventory.
    """
    import numpy as np
    import cv2
    target=cv2.GaussianBlur(np.rint(255*(final_strength if final_strength is not None else visible.astype(np.float32))).astype(np.uint8),(3,3),0)
    reference_target=cv2.GaussianBlur(np.rint(255*(reference_strength if reference_strength is not None else reference_visible.astype(np.float32))).astype(np.uint8),(3,3),0) if reference_visible is not None else None
    final_supported=cv2.dilate(visible.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    reference_supported=cv2.dilate(reference_visible.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool) if reference_visible is not None else None
    units=[];aliases={}
    for index,line in enumerate(fragment['source_lines'],1):
        fid=f"{fragment['id']}:line-{index}"
        b=pdf_box(line['box'],page);x,y,r,t=math.floor(b[0]),math.floor(b[1]),math.ceil(b[2]),math.ceil(b[3])
        alias_key=(tuple(line['box']),line['text'])
        if alias_key in aliases:
            units.append({'id':fid,'alias_of':aliases[alias_key],'text':line['text'],'source_box':[x,y,r,t]});continue
        aliases[alias_key]=fid
        x,y=max(0,x),max(0,y);r,t=min(original.shape[1],r),min(original.shape[0],t)
        pattern=np.any(original[y:t,x:r]<200,axis=2).astype(np.float32)
        unit={'id':fid,'text':line['text'],'source_box':[x,y,r,t],'predicted_box':[x+dx,y+dy,r+dx,t+dy], 'matched':False,'occurrences':[]}
        units.append(unit)
        if pattern.size==0 or not pattern.any() or pattern.shape[0]>visible.shape[0]:continue
        source_ink=pattern.astype(bool)
        predicted_retained=np.zeros(source_ink.shape,bool)
        px,py=x+dx,y+dy;lx,ty=max(0,-px),max(0,-py);rx,by=min(source_ink.shape[1],visible.shape[1]-px),min(source_ink.shape[0],visible.shape[0]-py)
        if rx>lx and by>ty:predicted_retained[ty:by,lx:rx]=final_supported[py+ty:py+by,px+lx:px+rx]
        cc,lab,stats,_=cv2.connectedComponentsWithStats(source_ink.astype(np.uint8),8)
        local_identity=True
        for ci in range(1,cc):
            cx,cy,cw,ch,_=map(int,stats[ci]);m=lab[cy:cy+ch,cx:cx+cw]==ci
            if predicted_retained[cy:cy+ch,cx:cx+cw][m].mean()<.90:local_identity=False;break
        unit['predicted_component_identity_passed']=local_identity
        # Identity retains source gray tone: a gray PDF glyph cell must not
        # collapse to an all-one binary template (NCC undefined/false-perfect).
        identity_pattern=1-original[y:t,x:r].min(axis=2).astype(np.float32)/255
        if float(identity_pattern.std())==0:
            unit['identity_incomplete']='constant-source-grayscale-template';continue
        pattern=cv2.GaussianBlur(np.rint(255*identity_pattern).astype(np.uint8),(3,3),0)
        if float(pattern.std())==0:
            unit['identity_incomplete']='constant-blurred-source-template';continue
        left=0;right=visible.shape[1]
        search=target[:,left:right]
        if search.shape[1]<pattern.shape[1]:continue
        scores=cv2.matchTemplate(search,pattern,cv2.TM_CCOEFF_NORMED)
        _,best,_,_=cv2.minMaxLoc(scores);unit['best_ncc']=best
        local_shape=None
        if final_strength is not None and px>=0 and py>=0 and px+source_ink.shape[1]<=visible.shape[1] and py+source_ink.shape[0]<=visible.shape[0]:
            local_shape=glyph_shape_evidence(identity_pattern,final_strength[py:py+source_ink.shape[0],px:px+source_ink.shape[1]])
            unit['source_projected_glyph_shape']=local_shape
        if best<.80 and not (local_shape and local_shape['passed']):continue
        # Count every peak above the fixed threshold in both source and final.
        # A new exact copy must not suppress a slightly degraded original.
        # Peak islands eliminate multiple 1px shifts of one physical instance.
        candidates=scores>=.80
        n,labels,stats,_=cv2.connectedComponentsWithStats(candidates.astype(np.uint8),8)
        occurrences=[]
        for component in range(1,n):
            cx,cy,cw,ch,_=map(int,stats[component]);local=scores[cy:cy+ch,cx:cx+cw].copy();local[labels[cy:cy+ch,cx:cx+cw]!=component]=-2
            _,score,_,at=cv2.minMaxLoc(local);ox,oy=left+cx+at[0],cy+at[1]
            candidate={'box':[ox,oy,ox+pattern.shape[1],oy+pattern.shape[0]],'ncc':score}
            if reference_supported is not None:
                actual_ink=visible[oy:oy+pattern.shape[0],ox:ox+pattern.shape[1]]
                independent_support=reference_supported[oy:oy+pattern.shape[0],ox:ox+pattern.shape[1]]
                candidate['source_ink_support']=float(independent_support[actual_ink].mean()) if actual_ink.any() else 0.
                candidate['added_ink_occurrence']=candidate['source_ink_support']<.90
                if reference_strength is not None and final_strength is not None:
                    shape=glyph_shape_evidence(reference_strength[oy:oy+pattern.shape[0],ox:ox+pattern.shape[1]],final_strength[oy:oy+pattern.shape[0],ox:ox+pattern.shape[1]])
                    candidate['same_site_source_glyph_shape']=shape
                    candidate['added_ink_occurrence']=not shape['passed']
            occurrences.append(candidate)
        if local_shape and local_shape['passed'] and not any(abs(o['box'][0]-px)<=1 and abs(o['box'][1]-py)<=1 for o in occurrences):
            occurrences.append({'box':[px,py,px+source_ink.shape[1],py+source_ink.shape[0]],'ncc':local_shape['ncc'],'added_ink_occurrence':False,'identity_method':'source-projected-glyph-shape','same_site_source_glyph_shape':local_shape})
        # Match the strongest identity. Only raster-identical NCC ties (1e-4)
        # use source proximity; similar different text cannot win on proximity.
        best_occurrences=[o for o in occurrences if o['ncc']>=best-1e-4]
        if not best_occurrences:best_occurrences=occurrences
        local_occurrences=[o for o in occurrences if abs(o['box'][0]-(x+dx))<=2 and abs(o['box'][1]-(y+dy))<=2]
        if local_identity and local_occurrences:best_occurrences=local_occurrences
        choice=min(best_occurrences,key=lambda o:(abs(o['box'][1]-(y+dy))+abs(o['box'][0]-(x+dx)),o['box'][1],o['box'][0]))
        shape_pass=True
        if final_strength is not None:
            bx,by,br,bt=choice['box'];chosen_shape=glyph_shape_evidence(identity_pattern,final_strength[by:bt,bx:br]);unit['chosen_glyph_shape']=chosen_shape;shape_pass=chosen_shape['passed']
        unit.update(matched=shape_pass,final_box=choice['box'],occurrences=occurrences)
        if reference_visible is not None:
            reference=reference_target[:,left:right]
            reference_scores=cv2.matchTemplate(reference,pattern,cv2.TM_CCOEFF_NORMED)
            _,reference_best,_,_=cv2.minMaxLoc(reference_scores)
            reference_candidates=reference_scores>=.80
            reference_count,_,_,_=cv2.connectedComponentsWithStats(reference_candidates.astype(np.uint8),8)
            unit['source_occurrence_count']=reference_count-1 if reference_best>=.80 else 0
            unit['source_occurrence_boxes']=[]
            if reference_best>=.80:
                _,reference_labels,reference_stats,_=cv2.connectedComponentsWithStats(reference_candidates.astype(np.uint8),8)
                for reference_component in range(1,reference_count):
                    cx,cy,cw,ch,_=map(int,reference_stats[reference_component]);local=reference_scores[cy:cy+ch,cx:cx+cw].copy();local[reference_labels[cy:cy+ch,cx:cx+cw]!=reference_component]=-2
                    _,_,_,at=cv2.minMaxLoc(local);ox,oy=left+cx+at[0],cy+at[1]
                    unit['source_occurrence_boxes'].append([ox,oy,ox+pattern.shape[1],oy+pattern.shape[0]])
    return units


def content_joining_metrics(gt, registrations):
    """Required source-line membership, unique identities, and actual adjacency."""
    fragment_rows={r['id']:r for r in registrations if 'line_units' in r}
    membership=[];unique=[];expected_edges=[];actual_edges=[];orders=[];unit_details=[]
    for group in gt['groups']:
        expected=[];actual_units=[];group_units=[]
        for fragment in sorted(group['fragments'],key=lambda f:f['reading_order']):
            row=fragment_rows.get(fragment['id'],{})
            observed={u['id']:u for u in row.get('line_units',[])}
            physical=[]
            for n,line in enumerate(fragment['source_lines'],1):
                uid=f"{fragment['id']}:line-{n}";unit=observed.get(uid,{'id':uid,'text':line['text'],'source_box':line['box'],'matched':False,'occurrences':[]})
                if unit.get('alias_of'):
                    unit_details.append(unit);continue
                physical.append(unit);group_units.append(unit)
                membership.append((uid,bool(unit.get('matched'))))
                if unit.get('matched'):actual_units.append(unit)
            expected.extend(geometric_unit_order(physical,'source_box'))
        # Compare actual identity occurrences with an independently rendered
        # source mask, including legitimate substring occurrences in longer lines.
        # A short annotated word such as "for" may also occur within other text;
        # those original occurrences are not output duplicates.
        identity_checks={}
        for unit in group_units:
            positions=[]
            for occurrence in unit.get('occurrences',[]):
                b=occurrence['box'];center=((b[0]+b[2])/2,(b[1]+b[3])/2)
                if not any(abs(center[0]-p[0])<=3 and abs(center[1]-p[1])<=3 for p in positions):positions.append(center)
            identity_checks[unit['id']]=bool(unit.get('matched')) and not any(o.get('added_ink_occurrence',False) and not (coverage(o['box'],unit.get('final_box',[0,0,0,0]))>.9 and coverage(unit.get('final_box',[0,0,0,0]),o['box'])>.9) for o in unit.get('occurrences',[]))
        # A shared final physical location cannot satisfy different identities.
        for i,unit in enumerate(actual_units):
            b=unit['final_box']
            if any(coverage(b,other['final_box'])>.9 and coverage(other['final_box'],b)>.9 for other in actual_units[:i]):
                identity_checks[unit['id']]=False
        unique.extend(identity_checks.items())
        extra_physical=[]
        for unit in group_units:
            extras=[o for o in unit.get('occurrences',[]) if o.get('added_ink_occurrence',False) and not (coverage(o['box'],unit.get('final_box',[0,0,0,0]))>.9 and coverage(unit.get('final_box',[0,0,0,0]),o['box'])>.9)]
            for n,occurrence in enumerate(extras,1):
                extra_id=unit['id']+':extra-occurrence-'+str(n);b=occurrence['box']
                # Different templates for the same exact physical box should not
                # duplicate the actual instance ledger.
                if any(coverage(b,u['final_box'])>.9 and coverage(u['final_box'],b)>.9 for u in extra_physical):continue
                extra_physical.append({'id':extra_id,'source_identity':unit['id'],'final_box':b,'matched':True})
        actual_units.extend(extra_physical)
        actual=geometric_unit_order(actual_units,'final_box')
        expected_edges.extend((a,b) for a,b in zip(expected,expected[1:]))
        actual_edges.extend((a,b) for a,b in zip(actual,actual[1:]))
        orders.append({'id':group['id']+':required-line-order','passed':actual==expected,'expected':expected,'actual':actual})
        unit_details.extend(group_units)
    expected_set=set(expected_edges);actual_set=set(actual_edges)
    return {'unit_uniqueness':fraction(unique),'edge_precision':fraction((a+'>'+b,(a,b) in expected_set) for a,b in actual_edges),
            'edge_recall':fraction((a+'>'+b,(a,b) in actual_set) for a,b in expected_edges)}, {
            'membership':fraction(membership),'units':unit_details,'orders':orders,
            'expected_edges':[list(x) for x in expected_edges],'actual_edges':[list(x) for x in actual_edges],
            'same_baseline_policy':'Geometric baseline bands then x; exact same source-box/text duplicates are aliases; repeated distinct physical text instances count separately.'}


def optional_guide_mask(gt, page_number, page, shape, character_boxes):
    """Only declared shared-mode rectangles; independent source glyphs win.

    Rectangles are rasterized by pixel-center membership without adaptive growth.
    Glyph guards include every pixel cell intersecting a nonspace character bbox.
    """
    import numpy as np
    mask=np.zeros(shape[:2],bool);protected=np.zeros(shape[:2],bool);audit=[]
    for box in character_boxes:
        b=pdf_box(box,page);x,y=max(0,math.floor(b[0])),max(0,math.floor(b[1]));r,t=min(shape[1],math.ceil(b[2])),min(shape[0],math.ceil(b[3]))
        if r>x and t>y:protected[y:t,x:r]=True
    for item in gt.get('optional_shared_guide_masks',[]):
        if item['page']!=page_number:continue
        b=pdf_box(item['box'],page)
        x,y=max(0,math.ceil(b[0]-.5)),max(0,math.ceil(b[1]-.5));r,t=min(shape[1],math.floor(b[2]-.5)+1),min(shape[0],math.floor(b[3]-.5)+1)
        if r>x and t>y:mask[y:t,x:r]=True
        audit.append({'id':item['id'],'pdf_box':item['box'],'raster_box':[x,y,r,t],
                      'positive_area_glyph_intersections':sum(intersect(item['box'],box)>0 for box in character_boxes)})
    requested=int(mask.sum());mask &= ~protected
    return mask,{'rectangles':audit,'requested_pixels':requested,'protected_glyph_pixels_subtracted':requested-int(mask.sum()),'ignored_pixels':int(mask.sum())}


def measure_final_pixels(gt, problems, pages, source, final_paths, output_dir, selected_ids=None):
    """Register independent source pixels to raw cutouts via three text anchors.

    Registration is independent of declared crop offsets: template locations in the
    actual raw crop must agree, then actual primary EDB alpha is tested in that grid.
    """
    import numpy as np
    import cv2
    document=fitz.open(source); source_pages={}; optional_masks={};mask_audits={}; rows=[]; recall=[]; seam=[]; precision=[]; incomplete=[]
    kernel=np.ones((3,3),np.uint8)
    for problem in problems:
        pid=problem['id']; pr=problem.get('passageRange',{})
        group=next((g for g in gt['groups'] if g['range']==[pr.get('start'),pr.get('end')]),None)
        if not group or (selected_ids is not None and pid not in selected_ids):
            final_path=final_paths.get(pid)
            if final_path:
                unexpected=Image.open(final_path).convert('RGBA')
                raw_path=Path(unquote(urlparse(problem.get('imagePath','')).path))
                if raw_path.is_file():unexpected=unexpected.resize(Image.open(raw_path).size,Image.Resampling.NEAREST)
                else:incomplete.append(pid+':unmatched-unit-source-grid-missing')
                unmatched,_=final_foreground(np.asarray(unexpected))
                precision.append({'id':pid,'numerator':0,'denominator':int(unmatched.sum()),'failed_ids':[pid+':unexpected-unit-ink']})
            else: incomplete.append(pid+':missing-final')
            continue
        raw_path=Path(unquote(urlparse(problem.get('imagePath','')).path)); final_path=final_paths.get(pid)
        if not raw_path.is_file() or not final_path:
            incomplete.append(pid+':missing-raw-or-final');continue
        raw=np.asarray(Image.open(raw_path).convert('RGB')); raw_gray=cv2.cvtColor(raw,cv2.COLOR_RGB2GRAY)
        # The actual published primary may use the raw grid while its preview
        # trims transparent borders. Never resample candidate pixels to a preview.
        reference_path=Path(final_path)
        reference_cutout=Image.open(reference_path).convert('RGBA')
        reference_foreground,_=final_foreground(np.asarray(reference_cutout))
        reference_target=cv2.GaussianBlur(reference_foreground.astype(np.float32),(3,3),0)
        actual=Image.open(final_path).convert('RGBA')
        actual_array=np.asarray(actual)
        visible,opaque_surfaces=final_foreground(actual_array)
        final_strength=actual_array[...,3].astype(np.float32)/255
        for sx,sy,sw,sh,_ in opaque_surfaces:
            final_strength[sy:sy+sh,sx:sx+sw]=1-actual_array[sy:sy+sh,sx:sx+sw,:3].min(axis=2).astype(np.float32)/255
        dilated=cv2.dilate(visible.astype(np.uint8),kernel).astype(bool)
        allowed=np.zeros(visible.shape,np.uint8); allowed_strength=np.zeros(visible.shape,np.float32); final_ignore=np.zeros(visible.shape,bool); mapped_fragments=[]; projected_order=[]
        for fragment in sorted(group['fragments'],key=lambda f:f['reading_order']):
            pageid=next(key for key in pages if int(key.rsplit('-',1)[1])==fragment['page']); page=pages[pageid]
            if pageid not in source_pages:
                pix=document[fragment['page']-1].get_pixmap(matrix=fitz.Matrix(page['metadata']['dpi']/72,page['metadata']['dpi']/72),alpha=False)
                arr=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
                c=page['metadata'].get('margin_crop_box')
                if c: arr=arr[c['top']:c['bottom'],c['left']:c['right']]
                source_pages[pageid]=arr
                character_boxes=[char['bbox'] for block in document[fragment['page']-1].get_text('rawdict')['blocks'] for line in block.get('lines',[]) for span in line.get('spans',[]) for char in span.get('chars',[]) if char.get('c','').strip()]
                optional_masks[pageid],mask_audits[pageid]=optional_guide_mask(gt,fragment['page'],page,arr.shape,character_boxes)
            original=source_pages[pageid]; gray=cv2.cvtColor(original,cv2.COLOR_RGB2GRAY)
            anchors=[]; cutout_anchors=[]; content=fragment['source_lines']
            # Use geographically separated lines; narrow labels are poor unique anchors.
            usable=[line for line in content if line['box'][2]-line['box'][0]>50]
            for index in sorted(set([0,len(usable)//2,len(usable)-1])) if usable else []:
                b=pdf_box(usable[index]['box'],page); x,y=math.floor(b[0]),math.floor(b[1]); r,t=math.ceil(b[2]),math.ceil(b[3])
                patch=gray[max(0,y):min(t,gray.shape[0]),max(0,x):min(r,gray.shape[1])]
                if patch.size==0 or patch.shape[0]>raw_gray.shape[0] or patch.shape[1]>raw_gray.shape[1]:continue
                scores=cv2.matchTemplate(raw_gray,patch,cv2.TM_CCOEFF_NORMED); _,score,_,location=cv2.minMaxLoc(scores)
                anchors.append({'score':score,'dx':location[0]-x,'dy':location[1]-y,'source_box':[x,y,r,t]})
                cutout_pattern=cv2.GaussianBlur((patch<200).astype(np.float32),(3,3),0)
                if cutout_pattern.shape[0]<=reference_target.shape[0] and cutout_pattern.shape[1]<=reference_target.shape[1]:
                    cutout_scores=cv2.matchTemplate(reference_target,cutout_pattern,cv2.TM_CCOEFF_NORMED)
                    _,cutout_score,_,cutout_location=cv2.minMaxLoc(cutout_scores)
                    # Repeated source text may have an exact added copy. Select
                    # the nearest fixed-threshold local peak to the independently
                    # established raw location; all three final translations must
                    # still agree and every required source component is checked.
                    py,px=np.where((cutout_scores>=.80)&(cutout_scores>=cv2.dilate(cutout_scores,np.ones((3,3),np.uint8))))
                    if len(px):
                        nearest=int(np.argmin((px-location[0])**2+(py-location[1])**2))
                        cutout_location=(int(px[nearest]),int(py[nearest]));cutout_score=float(cutout_scores[cutout_location[1],cutout_location[0]])
                    cutout_anchors.append({'score':cutout_score,'dx':cutout_location[0]-x,'dy':cutout_location[1]-y,'source_box':[x,y,r,t]})
            valid=len(anchors)>=min(3,len(usable)) and bool(anchors) and all(a['score']>=.95 for a in anchors)
            if valid:
                dx=int(round(float(np.median([a['dx'] for a in anchors]))));dy=int(round(float(np.median([a['dy'] for a in anchors]))))
                valid=all(abs(a['dx']-dx)<=1 and abs(a['dy']-dy)<=1 for a in anchors)
            raw_valid=valid
            valid=valid and len(cutout_anchors)==len(anchors) and bool(cutout_anchors) and all(a['score']>=.80 for a in cutout_anchors)
            if valid:
                dx=int(round(float(np.median([a['dx'] for a in cutout_anchors]))));dy=int(round(float(np.median([a['dy'] for a in cutout_anchors]))))
                valid=all(abs(a['dx']-dx)<=1 and abs(a['dy']-dy)<=1 for a in cutout_anchors)
            row={'id':fragment['id'],'anchors':anchors,'source_to_raw_valid':raw_valid,'cutout_anchors':cutout_anchors,'reference_cutout_path':str(reference_path),'reference_cutout_size':list(reference_cutout.size),'raw_size':[raw.shape[1],raw.shape[0]],'registration_valid':valid,'opaque_surfaces':opaque_surfaces}
            rows.append(row)
            if not valid: incomplete.append(fragment['id']+':registration-unproven');continue
            row.update(dx=dx,dy=dy)
            projected_order.append((fragment['id'],pdf_box(fragment['first_content_line_box'],page)[1]+dy))
            b=pdf_box(fragment['box'],page); x0,y0=max(0,math.floor(b[0])),max(0,math.floor(b[1]));x1,y1=min(gray.shape[1],math.ceil(b[2])),min(gray.shape[0],math.ceil(b[3]))
            source_mask=np.any(original[y0:y1,x0:x1]<200,axis=2) & ~optional_masks[pageid][y0:y1,x0:x1]
            # Clipping outside export is measured as loss, never removed from denominator.
            retained=np.zeros(source_mask.shape,bool); final_x,final_y=x0+dx,y0+dy
            lx,ty=max(0,-final_x),max(0,-final_y);rx,by=min(x1-x0,visible.shape[1]-final_x),min(y1-y0,visible.shape[0]-final_y)
            if rx>lx and by>ty:
                retained[ty:by,lx:rx]=dilated[final_y+ty:final_y+by,final_x+lx:final_x+rx]
                allowed[final_y+ty:final_y+by,final_x+lx:final_x+rx] |= source_mask[ty:by,lx:rx].astype(np.uint8)
                allowed_strength[final_y+ty:final_y+by,final_x+lx:final_x+rx]=1-original[y0+ty:y0+by,x0+lx:x0+rx].min(axis=2).astype(np.float32)/255
                final_ignore[final_y+ty:final_y+by,final_x+lx:final_x+rx] |= optional_masks[pageid][y0+ty:y0+by,x0+lx:x0+rx]
            # A verified translation is valid over this source segment, including
            # optional guide endpoints just outside conservative GT content boxes.
            for segment in problem.get('sourceSegments',[]):
                if segment.get('sourcePageId',segment.get('source_page_id'))!=pageid:continue
                sb=rect(segment['bbox'])
                if coverage(pdf_box(fragment['box'],page),sb)<.5:continue
                sx,sy=max(0,math.floor(sb[0])),max(0,math.floor(sb[1]));sr,st=min(original.shape[1],math.ceil(sb[2])),min(original.shape[0],math.ceil(sb[3]))
                ax,ay=max(sx,-dx),max(sy,-dy);ar,at=min(sr,visible.shape[1]-dx),min(st,visible.shape[0]-dy)
                if ar>ax and at>ay:final_ignore[ay+dy:at+dy,ax+dx:ar+dx] |= optional_masks[pageid][ay:at,ax:ar]
            count,labels,stats,_=cv2.connectedComponentsWithStats(source_mask.astype(np.uint8),8)
            retained_count=0; total_count=0; failures=[]
            for component in range(1,count):
                if stats[component,cv2.CC_STAT_AREA]<1:continue
                cx,cy,cw,ch,area=map(int,stats[component]);mask=labels[cy:cy+ch,cx:cx+cw]==component
                ratio=float(retained[cy:cy+ch,cx:cx+cw][mask].mean());ok=ratio>=.90
                cid=f"{fragment['id']}:component-{component}"; recall.append((cid,ok));total_count+=1;retained_count+=int(ok)
                if not ok:failures.append({'id':cid,'source_box':[x0+cx,y0+cy,cw,ch],'coverage':ratio,'area':area})
            row.update(source_components=total_count,retained_components=retained_count,failed_components=failures,
                       source_ink_pixels=int(source_mask.sum()),retained_ink_pixels=int((source_mask&retained).sum()))
            # Both first and last source text-line ink are checked independently at every boundary.
            for side,line in [('first',content[0]),('last',content[-1])] if content else []:
                bb=pdf_box(line['box'],page); ax,ay=max(x0,math.floor(bb[0])),max(y0,math.floor(bb[1]));ar,ab=min(x1,math.ceil(bb[2])),min(y1,math.ceil(bb[3]))
                mask=source_mask[ay-y0:ab-y0,ax-x0:ar-x0];ret=retained[ay-y0:ab-y0,ax-x0:ar-x0]
                if mask.any():seam.append((fragment['id']+':'+side,float(ret[mask].mean())>=.90))
            mapped_fragments.append(fragment['id'])
        # Optional range headers are legitimate context, not surplus passage ink.
        marker=group.get('marker',{}); marker_page=marker.get('page')
        for row in rows:
            if not row.get('registration_valid') or row['id'] not in mapped_fragments:continue
            f=next(f for f in group['fragments'] if f['id']==row['id'])
            if f['page']!=marker_page:continue
            pageid=next(key for key in pages if int(key.rsplit('-',1)[1])==marker_page);page=pages[pageid];original=source_pages[pageid]
            for marker_line in [marker]+marker.get('wrapped_header_lines',[]):
                if not isinstance(marker_line,dict) or 'box' not in marker_line:continue
                b=pdf_box(marker_line['box'],page);x,y,r,t=map(int,[math.floor(b[0]),math.floor(b[1]),math.ceil(b[2]),math.ceil(b[3])]); dx,dy=row['dx'],row['dy']
                if x+dx>=0 and y+dy>=0 and r+dx<=allowed.shape[1] and t+dy<=allowed.shape[0]:allowed[y+dy:t+dy,x+dx:r+dx] |= np.any(original[y:t,x:r]<200,axis=2).astype(np.uint8)
        for fragment in sorted(group['fragments'],key=lambda f:f['reading_order']):
            registration=next((row for row in rows if row['id']==fragment['id'] and row.get('registration_valid')),None)
            if registration:
                pageid=next(key for key in pages if int(key.rsplit('-',1)[1])==fragment['page']);page=pages[pageid]
                line_original=source_pages[pageid].copy();line_original[optional_masks[pageid]]=255
                registration['line_units']=observe_required_lines(fragment,page,line_original,visible & ~final_ignore,registration['dx'],registration['dy'],allowed.astype(bool),np.where(final_ignore,0,final_strength),np.where(final_ignore,0,allowed_strength))
        if projected_order:
            rows.append({'id':pid+':actual-fragment-order','registration_valid':True,'order_passed':reading_order_valid(projected_order),'positions':projected_order})
        # Precision is evaluated only if every fragment was registered, otherwise null.
        if len(mapped_fragments)==len(group['fragments']):
            tolerated=cv2.dilate(allowed,kernel).astype(bool)
            measured_visible=visible & ~final_ignore
            numerator=int((measured_visible&tolerated).sum()); denominator=int(measured_visible.sum())
            precision.append({'id':pid,'numerator':numerator,'denominator':denominator,'failed_ids':[pid+':surplus-ink'] if numerator<denominator else []})
            Image.fromarray(np.stack([visible*255,allowed*255,np.zeros(allowed.shape,np.uint8)],axis=2).astype(np.uint8)).save(output_dir/(pid+'-pixel-comparison.png'))
    # Missing GT units contribute actual absent source components to recall.
    represented={tuple(p.get('passageRange',{}).get(x) for x in ['start','end']) for p in problems if selected_ids is None or p['id'] in selected_ids}
    for group in gt['groups']:
        if tuple(group['range']) in represented:continue
        for fragment in group['fragments']:
            pageid=next(key for key in pages if int(key.rsplit('-',1)[1])==fragment['page']);page=pages[pageid]
            pix=document[fragment['page']-1].get_pixmap(matrix=fitz.Matrix(page['metadata']['dpi']/72,page['metadata']['dpi']/72),alpha=False)
            arr=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3);c=page['metadata'].get('margin_crop_box')
            if c:arr=arr[c['top']:c['bottom'],c['left']:c['right']]
            b=pdf_box(fragment['box'],page);x,y,r,t=math.floor(b[0]),math.floor(b[1]),math.ceil(b[2]),math.ceil(b[3])
            character_boxes=[char['bbox'] for block in document[fragment['page']-1].get_text('rawdict')['blocks'] for line in block.get('lines',[]) for span in line.get('spans',[]) for char in span.get('chars',[]) if char.get('c','').strip()]
            ignored,_=optional_guide_mask(gt,fragment['page'],page,arr.shape,character_boxes)
            mask=(np.any(arr[max(0,y):t,max(0,x):r]<200,axis=2) & ~ignored[max(0,y):t,max(0,x):r]).astype(np.uint8)
            n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
            recall.extend((fragment['id']+':missing-component-'+str(i),False) for i in range(1,n) if stats[i,cv2.CC_STAT_AREA]>=1)
            seam.extend([(fragment['id']+':first',False),(fragment['id']+':last',False)])
    for audit in mask_audits.values():
        incomplete.extend('optional-guide-mask:'+row['id']+':intersects-source-glyph' for row in audit['rectangles'] if row['positive_area_glyph_intersections'])
    if incomplete: return {'precision':None,'recall':None},None,{'registrations':rows,'incomplete':incomplete}
    precision_result={'numerator':sum(p['numerator'] for p in precision),'denominator':sum(p['denominator'] for p in precision),'failed_ids':[x for p in precision for x in p['failed_ids']]}
    if not precision_result['denominator']:precision_result=None
    return {'precision':precision_result,'recall':fraction(recall)},fraction(seam),{'registrations':rows,'precision_per_unit':precision,'optional_guide_masks':mask_audits,'incomplete':incomplete}


def measure_shared(ground_truth_path, source_id, session_path, pages_path, output_dir):
    ground_truth_path, session_path, pages_path, output_dir = map(Path, (ground_truth_path, session_path, pages_path, output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_bytes=ground_truth_path.read_bytes()
    truth = json.loads(ground_truth_bytes)
    gt = next(d for d in truth['documents'] if d['id'] == source_id)
    session = json.loads(session_path.read_text()); pages = {p['page_id']:p for p in json.loads(pages_path.read_text())}
    source = Path(gt['source']['path']); source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    groups = gt['groups']; problems = session['problems']
    assertions = []; details = {'units': [], 'excluded_intrusions': [], 'decoded_images': [], 'comparisons': []}
    def check(key, ok):
        assertions.append({'id':key, 'passed':bool(ok)})
    def page_for(number):
        return next(p for key,p in pages.items() if int(key.rsplit('-',1)[1]) == number)
    input_files = [Path(x) for x in session.get('input_files',[])]
    input_unchanged = bool(input_files) and all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==source_hash for p in input_files)
    check('input-unchanged', source_hash == gt['source']['sha256'] and input_unchanged)
    expected = {tuple(g['range']):g for g in groups}
    matches = {key:[] for key in expected}
    detection = []; expected_edges = []; actual_edges = []; uniqueness = []; lines = []
    for problem in problems:
        pid = problem['id']; pr = problem.get('passageRange', problem.get('passage_range')) or {}
        key = (pr.get('start'),pr.get('end')); group = expected.get(key)
        segments = problem.get('sourceSegments', [])
        if not segments:
            segments = [{'sourcePageId': problem.get('sourcePageId'), 'bbox': problem['bbox']}]
        mapped = []; region_match = False
        if group:
            for segment in segments:
                pageid = segment.get('sourcePageId', segment.get('source_page_id')); page = pages.get(pageid)
                candidates = []
                if page:
                    for frag in group['fragments']:
                        if page_for(frag['page'])['page_id'] == pageid:
                            score = coverage(pdf_box(frag['box'],page), rect(segment['bbox']))
                            candidates.append((score,frag['reading_order'],frag['id']))
                if candidates and max(candidates)[0] > .5:
                    mapped.extend(fid for score, order, fid in sorted(candidates, key=lambda c:c[1]) if score > .5); region_match = True
                else:
                    mapped.append('unmatched:'+pid+':'+str(len(mapped)))
        region_intersection=0.;region_union=0.
        for pageid,page in pages.items():
            required=[pdf_box(f['box'],page) for f in group['fragments'] if page_for(f['page'])['page_id']==pageid] if group else []
            selected=[rect(seg['bbox']) for seg in segments if seg.get('sourcePageId',seg.get('source_page_id'))==pageid]
            overlaps=[(max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])) for a in required for b in selected if intersect(a,b)>0]
            shared_area=union_area(overlaps);region_intersection+=shared_area
            region_union+=union_area(required)+union_area(selected)-shared_area
        iou=region_intersection/region_union if region_union else 0
        actual_edges.extend((pid+':'+a+'>'+b, (a,b)) for a,b in zip(mapped,mapped[1:]))
        details['units'].append({'id':pid, 'range':list(key), 'mapped_fragment_ids':mapped,'source_region_iou':iou})
    assignments,matched_unit_ids=assign_detections(details['units'],expected)
    detection=list(assignments.items())
    for key,pid in matched_unit_ids.items():matches[key].append(next(p for p in problems if p['id']==pid))
    for key,group in expected.items():
        candidates = matches[key]
        range_predictions=[u for u in details['units'] if tuple(u['range'])==key and u['source_region_iou']>=.8]
        uniqueness.append((group['id'], len(range_predictions)==1))
        fragments = sorted(group['fragments'], key=lambda f:f['reading_order'])
        expected_edges.extend((a['id'],b['id']) for a,b in zip(fragments,fragments[1:]))
        for fragment in fragments:
            page = page_for(fragment['page'])
            selected = [rect(s['bbox']) for p in candidates for s in p.get('sourceSegments',[]) if s.get('sourcePageId',s.get('source_page_id'))==page['page_id']]
            for n,line in enumerate(fragment['source_lines']):
                box = pdf_box(line['box'],page)
                # Exact text bounding boxes are evidence; approximate outer edges are not.
                score = max((coverage(box,b) for b in selected),default=0)
                lines.append((f"{fragment['id']}:line-{n+1}",score >= .995))
    expected_set = set(expected_edges); actual_set = {edge for _,edge in actual_edges}
    for role in ['question_exclusion_starts','choice_exclusion_starts']:
        for n,item in enumerate(gt.get(role,[])):
            page = page_for(item['page']); box = pdf_box(item['box'],page)
            for problem in problems:
                for segment in problem.get('sourceSegments',[]):
                    if segment.get('sourcePageId',segment.get('source_page_id')) == page['page_id'] and coverage(box,rect(segment['bbox'])) > .1:
                        details['excluded_intrusions'].append({'id':f'{role}:{n+1}', 'problem_id':problem['id'],'text':item['text']})
    # Actual binary inventory, decoded assets and normalized record geometry.
    final_paths={}
    edb_paths = sorted(session_path.parent.glob('*.edb'))
    check('edb-present', bool(edb_paths)); record_count = 0
    for edb in edb_paths:
        try:
            parsed = parse_edb(edb); check(edb.name+':parse', parsed.record_count_actual>0 and parsed.record_count_actual==parsed.record_count_hint)
            check(edb.name+':max-50-pages',1<=parsed.page_count_hint<=50)
            images = extract_images(parsed,edb,output_dir/edb.stem)
            part = next((p for p in session.get('edb_parts',[]) if Path(p.get('edbPath',p.get('edb_path',''))).name==edb.name),None)
            ordered_ids = part.get('problemIds',[]) if part else [p['id'] for p in problems]
            check(edb.name+':unit-record-correspondence',len(ordered_ids)==len(parsed.records))
            details['decoded_images'].extend(map(str,images)); previous = -math.inf
            for record in parsed.records:
                rid = edb.name+':record-'+str(record.index); record_count += 1
                if record.index < len(ordered_ids):
                    problem = next((p for p in problems if p['id']==ordered_ids[record.index]),None)
                    asset = Path(unquote(urlparse(problem.get('boardRenderPath','')).path)) if problem else Path('/nonexistent')
                    final_path = output_dir/edb.stem/f'record_{record.index:04d}_img_0.png'
                    if problem and final_path.is_file(): final_paths[problem['id']]=final_path
                    if asset.is_file() and final_path.is_file():
                        with Image.open(asset) as a, Image.open(final_path) as b:
                            b=b.convert('RGBA'); a=a.convert('RGBA').resize(b.size,Image.Resampling.LANCZOS)
                            def flatten(im):
                                canvas=Image.new('RGBA',im.size,'white'); canvas.alpha_composite(im); return canvas.convert('RGB')
                            error=max(ImageStat.Stat(ImageChops.difference(flatten(a),flatten(b))).mean)/255
                            details.setdefault('export_correspondence',[]).append({'record_id':rid,'problem_id':problem['id'],'max_channel_mean_abs_error':error,'tolerance':.004})
                    else: details.setdefault('export_correspondence',[]).append({'record_id':rid,'problem_id':problem['id'] if problem else None,'preview_comparison_incomplete':True})
                if not record.embedded_images:
                    check(rid+':image-only',False); continue
                values = [record.pos_x,record.pos_y,record.width_hint,record.height_hint]
                valid = all(v is not None and math.isfinite(v) for v in values)
                check(rid+':geometry',valid and min(values)>=0 and record.pos_x+record.width_hint<=1.00001 and record.pos_y+record.height_hint<=1.00001)
                check(rid+':nonoverlap-order',valid and record.pos_y>=previous-1e-6)
                if valid: previous = record.pos_y+record.height_hint
            for image in images:
                with Image.open(image) as decoded:
                    decoded.load(); check(str(image.relative_to(output_dir))+':readback',decoded.width>0 and decoded.height>0)
        except Exception as error:
            check(edb.name+':parse',False); details.setdefault('errors',[]).append(str(error))
    check('one-record-per-unit',record_count==len(problems))
    # Source-only overlays next to actual decoded exports; no claim of registered pixel diff.
    document = fitz.open(source)
    for number in sorted({f['page'] for g in groups for f in g['fragments']}):
        pix = document[number-1].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
        im = Image.frombytes('RGB',(pix.width,pix.height),pix.samples); draw=ImageDraw.Draw(im)
        page = page_for(number); meta=page['metadata']; scale=meta['dpi']/72; crop=meta.get('margin_crop_box',{})
        for g in groups:
            for f in g['fragments']:
                if f['page']==number: draw.rectangle(f['box'],outline='green',width=2)
        for problem in problems:
            for segment in problem.get('sourceSegments',[]):
                if segment.get('sourcePageId',segment.get('source_page_id'))==page['page_id']:
                    b=rect(segment['bbox']); b=[(v+crop.get('left' if i%2==0 else 'top',0))/scale for i,v in enumerate(b)]
                    draw.rectangle(b,outline='red',width=1)
        target=output_dir/f'source-selection-page-{number:03}.png'; im.save(target); details['comparisons'].append(str(target))
    try:
        pixel_metrics,seam_metrics,pixel_details=measure_final_pixels(gt,problems,pages,source,final_paths,output_dir,set(matched_unit_ids.values()))
    except ImportError as error:
        pixel_metrics={'precision':None,'recall':None};seam_metrics=None;pixel_details={'incomplete':[str(error)]}
    line_joining,line_details=content_joining_metrics(gt,pixel_details.get('registrations',[]))
    details['required_line_order']=line_details
    for order in line_details['orders']:check(order['id'],order['passed'])
    membership_failed=set((line_details['membership'] or {}).get('failed_ids',[]))
    # Export order is proved by independent original-source identities in each
    # actual decoded record. Preview tone equality is diagnostic, never order.
    registrations={r['id']:r for r in pixel_details.get('registrations',[]) if r.get('registration_valid')}
    for correspondence in details.get('export_correspondence',[]):
        pid=correspondence.get('problem_id');problem=next((p for p in problems if p['id']==pid),None)
        pr=problem.get('passageRange',{}) if problem else {};group=next((g for g in groups if g['range']==[pr.get('start'),pr.get('end')]),None)
        source_identity=bool(group) and all(f['id'] in registrations and all(u.get('matched') or u.get('alias_of') for u in registrations[f['id']].get('line_units',[])) and bool(registrations[f['id']].get('line_units')) for f in group['fragments'])
        check(correspondence['record_id']+':final-source-record-identity',source_identity)
        correspondence['source_record_identity_passed']=source_identity
        correspondence['preview_tone_error_is_diagnostic_only']=True
    uniqueness_failed=set((line_joining['unit_uniqueness'] or {}).get('failed_ids',[]))
    for unit in line_details['units']:
        if unit.get('alias_of'):continue
        check(unit['id']+':source-identity',unit['id'] not in membership_failed)
        check(unit['id']+':unique-physical-identity',unit['id'] not in uniqueness_failed)
    details['final_pixel_measurement']=pixel_details
    details['comparisons'].extend(str(p) for p in sorted(output_dir.glob('*-pixel-comparison.png')))
    for row in pixel_details.get('registrations',[]):
        if 'order_passed' in row: check(row['id'],row['order_passed'])
    actual_ranges=[tuple(p.get('passageRange',{}).get(x) for x in ['start','end']) for p in problems if p['id'] in set(matched_unit_ids.values())]
    check('source-unit-reading-order',actual_ranges==sorted(actual_ranges))
    metrics={'detection': {'precision':fraction(detection), 'recall':fraction((g['id'],bool(matches[k])) for k,g in expected.items())},
             'preservation': pixel_metrics,
             'joining': {**line_joining, 'seam_retention':seam_metrics},
             'layout': {'assertions':fraction((a['id'],a['passed']) for a in assertions)}}
    assertion_map={a['id']:a['passed'] for a in assertions}
    order_ok=all(a['passed'] for a in assertions if a['id'].endswith(':nonoverlap-order') or a['id'].endswith(':actual-fragment-order') or a['id'].endswith(':final-source-record-identity') or a['id']=='source-unit-reading-order' or a['id'].endswith(':required-line-order'))
    valid_edb=bool(edb_paths) and all(a['passed'] for a in assertions if a['id'].endswith(':parse') or a['id'].endswith(':readback') or a['id'].endswith(':unit-record-correspondence'))
    no_cut=bool(edb_paths) and all(a['passed'] for a in assertions if a['id'].endswith(':geometry') or a['id'].endswith(':nonoverlap-order') or a['id'].endswith(':max-50-pages'))
    preservation_known=pixel_metrics['recall'] is not None and seam_metrics is not None
    hard_checks=[{'id':'correct-order','passed':order_ok},
                 {'id':'valid-edb','passed':valid_edb},
                 {'id':'no-overlap-or-cut','passed':no_cut},
                 {'id':'no-unintended-merge','passed':all(ok for _,ok in detection)},
                 {'id':'no-forbidden-content','passed':not details['excluded_intrusions']},
                 {'id':'no-missing-instance','passed':all(matches.values()) and not membership_failed},
                 {'id':'no-duplicate-instance','passed':all(ok for _,ok in uniqueness) and not uniqueness_failed},
                 {'id':'input-unchanged','passed':source_hash==gt['source']['sha256'] and input_unchanged}]
    if preservation_known:
        hard_checks.append({'id':'no-meaningful-content-loss','passed':not pixel_metrics['recall']['failed_ids'] and not seam_metrics['failed_ids'] and not membership_failed})
    incomplete_reasons=list(pixel_details.get('incomplete',[]))
    for axis,components in metrics.items():
        for name,value in components.items():
            if value is None:incomplete_reasons.append(axis+'.'+name+': no measured denominator/evidence')
    if not preservation_known:incomplete_reasons.append('Final source component/seam evidence incomplete')
    # Runtime success/termination requires separate actual UI event traces.
    incomplete_reasons.append('External actual UI trials must establish task-complete and no-crash-or-oom; this observer does not infer runtime success from files.')
    report={'source_adjudication_sha256':gt.get('source_adjudication_sha256'),'measurement_policy':{'detection':'one-to-one range + source-region union IoU>=0.8','registration':'3 source-to-raw anchors NCC>=0.95 AND independently3 source-to-ACTUAL-FINAL-native-grid anchors NCC>=0.8;translations agree<=1px; candidate pixels NEVER resampled to preview/raw grid; unsupported scale remains incomplete','source_ink':'anyRGB<200','final_ink':'alpha>16;opaque 2D surfaces use anyRGB<200','spatial_tolerance_px':1,'internal_line_identity':'continuous8bit sourceinverseRGB versus actualalpha/opaqueRGB; constanttemplates rejected; localOtsu glyphshape NCC>=0.8 plus eachcomponent bidirectional>=90% within1px, source-onlyglyphbounds padded1px; periodic aliases require same-site independent sourceglyphshape','internal_line_ncc_min':.80,'internal_line_occurrences':'all fixed NCC>=0.8 peaks; added instances require actual ink support<0.9 from independently projected source (1px tolerance)','internal_line_x_search':'whole final shared unit','internal_line_y_search':'whole final shared unit','component_min_area_px':1,'component_retention_threshold':.90,'opaque_surface_min_extent_px':20,'opaque_surface_min_area_px':1000,'opaque_surface_min_fill':.85,'opaque_surface_min_opaque_alpha':245,'opaque_surface_min_opaque_fraction':.85,'opaque_rgb_material_evidence':'same alpha component contains actual RGBink<200 AND paperallRGB>=245; uniform chalkRGB preservesalpha semantics'},'schema_version':1,'source_id':source_id,'status':'incomplete' if incomplete_reasons else 'completed','metrics':metrics,'assertions':assertions,
            'hard_checks':hard_checks,'hard_failures':[x['id'] for x in hard_checks if not x['passed']],
            'incomplete_reasons':incomplete_reasons,
            'raw_measures':{'source_line_membership':line_details['membership'],'source_line_geometric_recall':fraction(lines),'excluded_start_intrusion_count':len(details['excluded_intrusions'])},
            'details':details, 'source_sha256':source_hash,'ground_truth_sha256':hashlib.sha256(ground_truth_bytes).hexdigest()}
    (output_dir/'raw-metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ['ground-truth','source-id','session','pages','output-dir']: parser.add_argument('--'+arg,required=True)
    args=parser.parse_args(); report=measure_shared(args.ground_truth,args.source_id,args.session,args.pages,args.output_dir)
    print(json.dumps({k:report[k] for k in ['source_id','status','metrics','raw_measures','hard_failures','incomplete_reasons']},ensure_ascii=False,indent=2))
    return 2 if report['status']=='incomplete' else bool(report['hard_failures'])

if __name__=='__main__': raise SystemExit(main())

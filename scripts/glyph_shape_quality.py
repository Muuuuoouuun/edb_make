"""Source-projected glyph identity; no masks or pixel-score threshold changes."""
import cv2
import numpy as np

def glyph_shape_evidence(source_strength, actual_strength):
    """Compare local glyph silhouettes at native source-projected coordinates.

    Independent Otsu foreground separation removes local gray material tone.
    Bounds come only from the source silhouette, padded by the registered 1px
    tolerance. A surrounding image edge cannot stand in for a source character.
    Constant source/actual patches are unidentifiable, never perfect matches.
    """
    result={'passed':False,'ncc':None,'source_component_retention':[],'actual_component_support':[]}
    if source_strength.shape!=actual_strength.shape or not source_strength.size:return result
    source=np.rint(np.clip(source_strength,0,1)*255).astype(np.uint8)
    actual=np.rint(np.clip(actual_strength,0,1)*255).astype(np.uint8)
    if not np.ptp(source) or not np.ptp(actual):result['reason']='constant-patch';return result
    source_cut,sm=cv2.threshold(source,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    actual_cut,am=cv2.threshold(actual,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    if not sm.any() or np.all(sm) or not am.any() or np.all(am):result['reason']='unidentifiable-silhouette';return result
    x,y,w,h=cv2.boundingRect(sm);x0,y0=max(0,x-1),max(0,y-1);x1,y1=min(sm.shape[1],x+w+1),min(sm.shape[0],y+h+1)
    sm=sm[y0:y1,x0:x1];am=am[y0:y1,x0:x1]
    if not sm.std() or not am.std():result['reason']='constant-silhouette';return result
    ncc=float(cv2.matchTemplate(cv2.GaussianBlur(am,(3,3),0),cv2.GaussianBlur(sm,(3,3),0),cv2.TM_CCOEFF_NORMED)[0,0])
    kernel=np.ones((3,3),np.uint8);ss=cv2.dilate((sm>0).astype(np.uint8),kernel).astype(bool);aa=cv2.dilate((am>0).astype(np.uint8),kernel).astype(bool)
    for mask,supported,key in [(sm,aa,'source_component_retention'),(am,ss,'actual_component_support')]:
        count,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
        for cc in range(1,count):
            cx,cy,cw,ch,area=map(int,stats[cc]);ratio=float(supported[cy:cy+ch,cx:cx+cw][labels[cy:cy+ch,cx:cx+cw]==cc].mean())
            result[key].append({'area':area,'ratio':ratio})
    result.update(ncc=ncc,source_otsu=source_cut,actual_otsu=actual_cut,source_glyph_bounds=[x0,y0,x1,y1])
    result['passed']=ncc>=.8 and all(c['ratio']>=.9 for c in result['source_component_retention']+result['actual_component_support'])
    return result

"""One immutable visual layout per batch; absent markers mean legacy normal."""
REVISION = 'visual_layout_v1'
LABELS = {'正常画面': 'normal', '对叠画面': 'stacked'}
DEFAULTS = dict(visual_layout_revision=REVISION, visual_layout='normal')


def settings(cfg):
    value = cfg.get('visual_layout', 'normal')
    if value not in LABELS.values() or cfg.get('visual_layout_revision', REVISION) != REVISION:
        raise ValueError('剪辑方式无效，请选择正常画面或对叠画面')
    result = dict(revision=REVISION, mode=value)
    if cfg.get('material_format') == 'sk_diverse':
        result['fit'] = cfg.get('sk_fit', 'contain')
    return result


def freeze(item, cfg):
    if 'visual_layout' in cfg:
        expected = settings(cfg)
        if 'visual_layout_snapshot' in item and item['visual_layout_snapshot'] != expected:
            raise ValueError('剪辑方式与已冻结批次不一致，不能改变本批画面')
        item['visual_layout_snapshot'] = expected


def validate(item, cfg):
    if 'visual_layout' in cfg or 'visual_layout_snapshot' in item:
        if item.get('visual_layout_snapshot') != settings(cfg):
            raise ValueError('剪辑方式快照缺失或被修改，请重新生成组合方案')


def label(item):
    mode = item.get('visual_layout_snapshot', {}).get('mode', 'normal')
    return next(k for k, v in LABELS.items() if v == mode)


def finalize(batch):
    from editorial import finalize as editorial_finalize
    batch=editorial_finalize(batch)
    from edit_style import freeze as freeze_edit_style
    for item in batch['items']:freeze_edit_style(item,batch['config'])
    for item in batch['items']:
        previous=item.get('visual_layout_snapshot')
        freeze(item,batch['config'])
        if previous is None and item.get('business_digest'):
            from grouped_planner import business_digest
            item['business_digest']=business_digest(item)
    from sk_selection import finalize as finalize_sk
    return finalize_sk(batch)

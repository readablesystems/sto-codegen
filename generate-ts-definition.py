#!/usr/env/bin python3

from xml.etree.ElementTree import parse


class TSGenSyntaxError(Exception):
    pass


def is_positive_integer(s):
    try:
        return int(s) > 0
    except ValueError:
        return False


def type_to_ctype(typename, width, extent):
    if typename == 'int' or typename == 'uint':
        if width in ['8', '16', '32', '64']:
            return typename + width
    elif typename == 'vchar':
        if is_positive_integer(width):
            return 'var_string<{}>'.format(int(width))
    elif typename == 'fchar':
        if is_positive_integer(width):
            return 'fix_string<{}>'.format(int(width))
    elif typename == 'fchar-arr':
        if is_positive_integer(width) and extent != '':
            return 'std::array<fix_string<{}>, {}>'.format(int(width), extent)
    elif typename == 'cpplistu64':
        return 'std::list<uint64>'

    raise TSGenSyntaxError(
        'invalid typename width combination: {}, {}'.format(typename, width))


def check_tag(node, expected_name):
    if node.tag != expected_name:
        raise TSGenSyntaxError(
            'tag mismatch: expected {}, got {}.'.format(expected_name,
                                                        node.tag))


def process_tree(all_definitions):
    check_tag(all_definitions, 'ts-split-definition')
    for record in all_definitions:
        process_record(record)


def generate_map_body(start_index_list):
    if not start_index_list:
        raise TSGenSyntaxError('at least one split must exist.')
    if len(start_index_list) == 1:
        return ['(void)col_n;', 'return 0;']
    output_lines = []
    for i, index in enumerate(start_index_list):
        line = ''
        if i == 0:
            line += 'if'
        elif (i + 1) == len(start_index_list):
            line += 'else'
        else:
            line += 'else if'

        if line == 'if' or line == 'else if':
            line += ' (col_n < {nsi}) return {si};'.format(
                nsi=start_index_list[i + 1], si=i)
        else:
            line += ' return {si};'.format(si=i)
        output_lines.append(line)
    return output_lines


def process_record(record):
    check_tag(record, 'record')

    record_name = record.attrib['name']

    all_fields = set()

    # contain tuples: (field index, ctype, field name)
    fields_by_splits = []
    split_names = []

    sidx = 0
    fidx = 0
    for split in record:
        check_tag(split, 'split')
        split_names.append(split.attrib['name'])

        split_fields = []
        for field in split:
            field_name = field.attrib['name']
            field_type = field.attrib['type']
            field_width = field.attrib.get('width', '0')
            field_extent = field.attrib.get('extent', '')
            if field_name in all_fields:
                raise TSGenSyntaxError(
                    'duplicate field name: {}'.format(field_name))
            all_fields.add(field_name)
            split_fields.append(
                (fidx, type_to_ctype(field_type, field_width, field_extent), field_name))
            fidx += 1
        fields_by_splits.append(split_fields)

        sidx += 1

    record_splitparams_template = """
template <>
struct SplitParams<{0}> {{
  using split_type_list = std::tuple<{1}>;
  using layout_type = typename SplitMvObjectBuilder<split_type_list>::type;
  static constexpr size_t num_splits = std::tuple_size<split_type_list>::value;

  static constexpr auto split_builder = std::make_tuple({2}
  );

  static constexpr auto split_merger = std::make_tuple({3}
  );

  static constexpr auto map = [](int col_n) -> int {{
    {4}
  }}
}};
"""

    split_builder_lambda_template = """
    [](const {rt}& in) -> {st} {{
      {st} out;
      {ln}
      return out;
    }}"""

    split_merger_lambda_template = """
    []({rt}* out, const {st}& in) -> void {{
      {ln}
    }}"""

    type_list = ', '.join(split_names)

    split_builders = []
    split_mergers = []
    level1_line_sep = '\n' + ' ' * 2
    lambda_line_sep = '\n' + ' ' * 6
    map_line_sep = '\n' + ' ' * 4

    split_field_idx_starts = []

    for i, fields in enumerate(fields_by_splits):
        split_field_idx_starts.append(fields[0][0])
        builder_enum_fields = []
        merger_enum_fields = []
        for field in fields:
            builder_enum_fields.append('out.{0} = in.{0};'.format(field[2]))
            merger_enum_fields.append('out->{0} = in.{0};'.format(field[2]))
        builder_lines = lambda_line_sep.join(builder_enum_fields)
        merger_lines = lambda_line_sep.join(merger_enum_fields)
        split_builders.append(
            split_builder_lambda_template.format(st=split_names[i],
                                                 rt=record_name,
                                                 ln=builder_lines))
        split_mergers.append(
            split_merger_lambda_template.format(st=split_names[i],
                                                rt=record_name,
                                                ln=merger_lines))

    map_body_lines = map_line_sep.join(
        generate_map_body(split_field_idx_starts))

    print(record_splitparams_template.format(record_name, type_list,
                                             ','.join(split_builders),
                                             ','.join(split_mergers),
                                             map_body_lines))

    record_accessor_template = """
template <typename A>
class RecordAccessor<A, {vt}> {{
 public:
  {fieldaccessors}

  void copy_into({vt}* dst) const {{
    return impl().copy_into_impl(dst);
  }}

 private:
  const A& impl() const {{
    return *static_cast<const A*>(this);
  }}
}};

template <>
class UniRecordAccessor<{vt} : public RecordAccessor<UniRecordAccessor<{vt}>, {vt}> {{
 public:
  UniRecordAccessor(const {vt}* const vptr) : vptr_(vptr) {{}}

 private:
  {unifieldimpls}

  {unicopyimpl}

  const {vt}* vptr_;
  friend RecordAccessor<UniRecordAccessor<{vt}>, {vt}>;
}};

template <>
class SplitRecordAccessor<{vt}> : public RecordAccessor<SplitRecordAccessor<{vt}>, {vt}> {{
 public:
   static constexpr size_t num_splits = SplitParams<{vt}>::num_splits;

   SplitRecordAccessor(const std::array<void*, num_splits>& vptrs)
     : {splitctorbody} {{}}

 private:
  {splitfieldimpls}

  {splitcopyimpl}

  {splitvptrs}

  friend RecordAccessor<SplitRecordAccessor<{vt}>, {vt}>;
}};
"""

    field_accessor_template = """
  const {fieldtype}& {fieldname}() const {{
    return impl().{fieldname}_impl();
  }}
"""

    uni_field_impl_template = """
  const {fieldtype}& {fieldname}_impl() const {{
    return vptr_->{fieldname};
  }}
"""

    split_field_impl_template = """
  const {fieldtype}& {fieldname}_impl() const {{
    return vptr_{splitno}_->{fieldname};
  }}
"""

    copy_impl_template = """
  void copy_into_impl({vt}* dst) const {{
    {copyimpllines}
  }}
"""
    uni_copy_lines_template = """
    if (vptr_) {{
      {copyfields}
    }}"""

    split_copy_group_template = """
    if (vptr_{splitno}_) {{
      {copyfields}
    }}
"""
    uni_copy_line_template = """dst->{fieldname} = vptr_->{fieldname};"""
    split_copy_line_template = """dst->{fieldname} = vptr_{splitno}_->{fieldname};"""

    split_copy_impl_template = """
"""

    split_ctor_line_template = """vptr_{splitno}_(reinterpret_cast<{splitname}*>(vptrs[{splitno}]))"""
    split_vptr_template = """const {splitname}* vptr_{splitno}_;"""

    field_accessors = []
    uni_field_impls = []
    split_field_impls = []
    split_ctor_lines = []
    split_vptrs = []
    uni_copy_lines = []
    split_copy_lines_by_group = []
    for sidx, fields in enumerate(fields_by_splits):
        split_copy_line_group = []
        split_ctor_lines.append(split_ctor_line_template.format(splitno=sidx, splitname=split_names[sidx]))
        split_vptrs.append(split_vptr_template.format(splitno=sidx, splitname=split_names[sidx]))
        for field in fields:
            field_accessors.append(field_accessor_template.format(fieldtype=field[1], fieldname=field[2]))
            uni_field_impls.append(uni_field_impl_template.format(fieldtype=field[1], fieldname=field[2]))
            split_field_impls.append(split_field_impl_template.format(fieldtype=field[1], fieldname=field[2], splitno=sidx))

            uni_copy_lines.append(uni_copy_line_template.format(fieldname=field[2]))
            split_copy_line_group.append(split_copy_line_template.format(fieldname=field[2], splitno=sidx))
        split_copy_lines_by_group.append(split_copy_group_template.format(splitno=sidx, copyfields=lambda_line_sep.join(split_copy_line_group)))

    field_accessors_code = level1_line_sep.join(field_accessors)
    uni_field_impl_code = level1_line_sep.join(uni_field_impls)
    split_field_impl_code = level1_line_sep.join(split_field_impls)
    split_ctor_code = ', '.join(split_ctor_lines)
    uni_copy_impl_code = copy_impl_template.format(vt=record_name, copyimpllines=uni_copy_lines_template.format(copyfields=lambda_line_sep.join(uni_copy_lines)))
    split_copy_impl_code = copy_impl_template.format(vt=record_name, copyimpllines=map_line_sep.join(split_copy_lines_by_group))
    split_vptrs_code = level1_line_sep.join(split_vptrs)

    record_accessor_code = record_accessor_template.format(vt=record_name, fieldaccessors=field_accessors_code, unifieldimpls=uni_field_impl_code,
                                                           unicopyimpl=uni_copy_impl_code, splitctorbody=split_ctor_code, splitfieldimpls=split_field_impl_code,
                                                           splitcopyimpl=split_copy_impl_code, splitvptrs=split_vptrs_code)
    print(record_accessor_code)


if __name__ == '__main__':
    tree = parse('split-ts-definition.xml')
    root = tree.getroot()
    process_tree(root)

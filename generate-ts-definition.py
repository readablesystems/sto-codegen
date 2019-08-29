#!/usr/env/bin python3

from xml.etree.ElementTree import parse


class TSGenSyntaxError(Exception):
    pass


def is_positive_integer(s):
    try:
        return int(s) > 0
    except ValueError:
        return False


def type_to_ctype(typename, width):
    if typename == 'int' or typename == 'uint':
        if width in ['8', '16', '32', '64']:
            return typename + width
    elif typename == 'vchar':
        if is_positive_integer(width):
            return 'fix_string<{}>'.format(int(width))
    else:
        raise TSGenSyntaxError(
            'invalid typename width combination: {}, {}'.format(typename,
                                                                width))


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
            field_width = field.attrib['width']
            if field_name in all_fields:
                raise TSGenSyntaxError(
                    'duplicate field name: {}'.format(field_name))
            all_fields.add(field_name)
            split_fields.append(
                (fidx, type_to_ctype(field_type, field_width), field_name))
            fidx += 1
        fields_by_splits.append(split_fields)

        sidx += 1

    record_splitparams_template = """
template <>
struct bench::SplitParams<{0}> {{
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


if __name__ == '__main__':
    tree = parse('split-ts-definition.xml')
    root = tree.getroot()
    process_tree(root)

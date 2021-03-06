from common.layers import conv2d, batchnorm, prelu, dilated_conv, max_pool, spatial_dropout, relu, deconv, unpool_without_mask, norm
import tensorflow as tf



def _prelu_bn(name, is_training, inputs, alpha_init=0.0, decay=0.99):
    """
    combine the batch norm and prelu together
    :param name: 
    :param inputs: 
    :param is_training: 
    :param alpha_init: 
    :return: 
    """
    bn = batchnorm(name+'_bn', is_training, inputs, decay)
    return prelu(name+'_prelu', bn, alpha_init)



def _bottleneck_encoder(name, is_training, inputs, input_channels, output_channels, internal_scale=4, asy=0, dilated=0,
                downsample=False, dropout_ratio=0.01, bn_decay=0.99, wd=2e-4, weight_init='xavier'):
    """
    :param name: 
    :param inputs: 
    :param output_channels: 
    :param internal_scale: 
    :param asy: 
    :param dilated: 
    :param downsample: 
    :param dropout_ratio: 
    :return: 
    """
    with tf.variable_scope(name) as scope:
        # the main branch, downsample the scale
        internal_channels = output_channels / internal_scale

        # the 1 x 1 projection or 2 x 2 if downsampleing
        kernel_size = 2 if downsample else 1
        main_branch = conv2d(name+'_main_unit1', inputs, input_channels, internal_channels, kernel_size, kernel_size,
                      bias_var=None, wd=wd, weight_initializer=weight_init)
        # the prelu_bn unit
        main_branch = _prelu_bn(name+'_main_unit1', is_training, main_branch, decay=bn_decay)

        # the conv unit according to the type
        if not asy and not dilated:
            main_branch = conv2d(name+'_main_unit2', main_branch, internal_channels, internal_channels, 3, 1,
                                 bias_var=None, wd=wd, weight_initializer=weight_init)
        elif asy:
            main_branch = conv2d(name+'_main_unit21', main_branch, internal_channels, internal_channels, [1, asy],
                                 1, bias_var=None, wd=wd, weight_initializer=weight_init)
            main_branch = conv2d(name+'_main_unit22', main_branch, internal_channels, internal_channels, [asy, 1],
                                 1, bias_var=None, wd=wd, weight_initializer=weight_init)
        elif dilated:
            main_branch = dilated_conv(name+'_main_unit2', main_branch, internal_channels, internal_channels, 3,
                                       dilated, bias_var=None, wd=wd, weight_initializer=weight_init)
        else:
            raise Exception("Error for bottleneck {}".format(name))
        main_branch = _prelu_bn(name+'_main_unit2', is_training, main_branch, decay=bn_decay)

        # the 1 x 1 to recover the ori channel num
        main_branch = conv2d(name+'_main_unit3', main_branch, internal_channels, output_channels, 1, 1,
                             bias_var=None, wd=wd, weight_initializer=weight_init)
        main_branch = batchnorm(name+'_main_unit3_bn', is_training, main_branch, decay=bn_decay)
        # the regularizar, spatial dropout
        main_branch = spatial_dropout(main_branch, is_training, dropout_ratio)

        # the other branch can be maxpooling and padding or nothing
        other = inputs
        if downsample:
            other = max_pool(other, 2, 2)
            # zero padding to match the main branch, use concat to do the zero paddings to channel
            batches, height, width, channels = other.get_shape().as_list()
            padding_channels = output_channels - channels
            padding = tf.zeros([batches, height, width, padding_channels], dtype=tf.float32)
            # padding to the other
            other = tf.concat([other, padding], axis=3)
        # add the main_branch and other branch together
        out = tf.add(main_branch, other)
        # after a prelu init, return
        return prelu(name+'_out', out)


def _bottleneck_decoder(name, is_training, inputs, input_channels, output_channels, internal_scale=4,
                        upsample=False, reverse_module=False, bn_decay=0.99, wd=2e-4, weight_init='xavier'):
    with tf.variable_scope(name) as scope:
        internal_channels = output_channels / internal_scale

        # the main branch
        main_branch = conv2d(name+'_main_unit1', inputs, input_channels, internal_channels,
                             1, 1, bias_var=None, wd=wd, weight_initializer=weight_init)
        main_branch = batchnorm(name+'_main_unit1_bn', is_training, main_branch,
                                decay=bn_decay)
        main_branch = relu(main_branch)

        # the second conv, decide by upsample or not
        if upsample:
            main_branch = deconv(name+'_main_unit2', main_branch, internal_channels,
                                 internal_channels, 3, 2, bias_var=None, wd=wd, weight_initializer=weight_init)
        else:
            main_branch = conv2d(name+'_main_unit2', main_branch, internal_channels,
                                 internal_channels, 3, 1, bias_var=None, wd=wd, weight_initializer=weight_init)
        main_branch = batchnorm(name+'_main_unit2_bn', is_training, main_branch,
                                decay=bn_decay)
        main_branch = relu(main_branch)
        # the third branch
        main_branch = conv2d(name+'_main_unit3', main_branch, internal_channels, output_channels,
                             1, 1, bias_var=None, wd=wd, weight_initializer=weight_init)

        # the other branch
        other = inputs
        if input_channels != output_channels or upsample:
            other = conv2d(name+'_other_unit1', other, input_channels,
                           output_channels, 1, 1, bias_var=None, wd=wd, weight_initializer=weight_init)
            other = batchnorm(name+'_other_unit1_bn', is_training, other, decay=bn_decay)
            if upsample and reverse_module:
                other = unpool_without_mask(other)

        if not upsample or reverse_module:
            main_branch = batchnorm(name+'_main_unit3_bn', is_training, main_branch, decay=bn_decay)
        else:
            return main_branch

        out = tf.add(main_branch, other)
        return relu(out)

def _initial_block(name, inputs, input_channels=3, output_channel=13, kerne=3, stride=2, wd=2e-4, weight_init='xavier'):
    conv = conv2d(name+'_conv_unit', inputs, input_channels, output_channel,
                  kerne, stride, bias_var=None, wd=wd, weight_initializer=weight_init)
    pool = max_pool(inputs, 2, 2)
    out = tf.concat([conv, pool], axis=3)
    return out


def build_encoder(images, is_training, num_classes=None):
    # the init block
    small_xavier_init = 'xavier_scale2'
    xavier_init = 'xavier'
    msra_init = 'msra_scale2'
    encode = _initial_block('initial', images, weight_init=xavier_init)
    # the bottleneck 1.0
    encode = _bottleneck_encoder('bottleneck1.0', is_training, encode, 16, 64,
                                 downsample=True, dropout_ratio=0, weight_init=xavier_init)
    # the bottleneck 1.1-1.4
    for i in range(4):
        encode = _bottleneck_encoder('bottleneck1.{}'.format(i+1), is_training, encode, 64, 64,
                                     dropout_ratio=0, weight_init=xavier_init)
    # the bottleneck2.0
    encode = _bottleneck_encoder('bottleneck2.0', is_training, encode, 64, 128,
                                 downsample=True, dropout_ratio=0, weight_init=xavier_init)

    # the bottleneck2.1-2.8, 3.1-3.8
    for i in range(2):
        encode = _bottleneck_encoder('bottleneck{}.1'.format(i+2), is_training, encode, 128, 128, weight_init=xavier_init)
        encode = _bottleneck_encoder('bottleneck{}.2'.format(i+2), is_training, encode, 128, 128, dilated=2, weight_init=xavier_init)
        encode = _bottleneck_encoder('bottleneck{}.3'.format(i+2), is_training, encode, 128, 128, asy=5, weight_init=xavier_init)
        encode = _bottleneck_encoder('bottleneck{}.4'.format(i+2), is_training, encode, 128, 128, dilated=4, weight_init=xavier_init)
        encode = _bottleneck_encoder('bottleneck{}.5'.format(i+2), is_training, encode, 128, 128, weight_init=xavier_init)
        encode = _bottleneck_encoder('bottleneck{}.6'.format(i+2), is_training, encode, 128, 128, dilated=8, weight_init=xavier_init)
        encode = _bottleneck_encoder('bottleneck{}.7'.format(i+2), is_training, encode, 128, 128, asy=5, weight_init=xavier_init )
        encode = _bottleneck_encoder('bottleneck{}.8'.format(i+2), is_training, encode, 128, 128, dilated=16, weight_init=xavier_init)

    if num_classes is not None:
        # train the encoder first
        encode = conv2d('prediction', encode, 128, num_classes, 1, 1, bias_var=0.1, wd=0, weight_initializer=small_xavier_init if num_classes == 3 else xavier_init)
        if num_classes == 3:
            encode = norm(encode)

    return encode


def build_decoder(encoder, is_training=True, num_classes=20):
    # upsamle model
    # norm_weight_init = tf.truncated_normal_initializer(0.001)
    xavier_init = 'xavier'
    small_xavier_init = 'xavier_scale2'
    decode = _bottleneck_decoder('bottleneck4.0', is_training, encoder, 128, 64, upsample=True, reverse_module=True, weight_init=xavier_init)
    decode = _bottleneck_decoder('bottleneck4.1', is_training, decode, 64, 64, weight_init=xavier_init)
    decode = _bottleneck_decoder('bottleneck4.2', is_training, decode, 64, 64, weight_init=xavier_init)
    decode = _bottleneck_decoder('bottleneck5.0', is_training, decode, 64, 16, upsample=True, reverse_module=True, weight_init=xavier_init)
    decode = _bottleneck_decoder('bottleneck5.1', is_training, decode, 16, 16, weight_init=xavier_init)
    # the output
    out = deconv('prediction', decode, 16, num_classes, 2, 2, bias_var=None, wd=2e-4, weight_initializer=xavier_init)
    if num_classes == 3:
        out = norm(out)
    return out









